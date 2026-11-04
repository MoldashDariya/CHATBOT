"""Cleaned text in, verified `Summary` out: one Claude call with a JSON schema, then code checks.

Design notes (see ARCHITECTURE.md, "Working with the LLM"):
- Structured output via `output_config.format`, not forced tool use (newer models reject a forced
  `tool_choice`). No `temperature`/`thinking`: current models reject sampling parameters and run
  adaptive thinking on their own; `LLM_EFFORT` sets how hard they think.
- One call, no chunking: `MAX_CHARS` keeps the text far below the context window, and one pass
  sees the whole document (chunk-and-merge would only add ways to lose a deadline).
- The page is data. It sits in a delimited block of the *user* message, delimiters inside it
  are neutralised, and the system prompt tells the model not to obey it.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import anthropic
from anthropic.types import MessageParam, OutputConfigParam
from pydantic import ValidationError

from bot.config import Effort, Settings
from bot.errors import LLMError
from bot.extractors.base import ExtractedContent
from bot.pipeline.schema import Confidence, LLMSummary, Summary
from bot.pipeline.verify import filter_links, strip_urls, verify_deadlines

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8000
LLM_TIMEOUT_S = 180.0
LANGUAGE_NAMES = {"ru": "Russian", "en": "English", "kk": "Kazakh"}

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
# Our own tags, as they could appear inside the page text to break out of the data block.
_OWN_TAG_RE = re.compile(r"<(/?)\s*(source_document|task)\b", re.IGNORECASE)
_MAX_ERROR_DETAILS = 5


@lru_cache
def load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_message(content: ExtractedContent, *, language: str, today: date) -> str:
    """The `<task>` block (trusted, ours) followed by the page inside `<source_document>`."""
    # The URL can come from a redirect header, i.e. from the site itself.
    url = content.source_url.replace("<", "%3C").replace(">", "%3E")
    body = _OWN_TAG_RE.sub(r"[\1\2", content.text)
    title = _OWN_TAG_RE.sub(r"[\1\2", content.title) if content.title else ""
    title_line = f"Page title: {title}\n\n" if title else ""
    return (
        "<task>\n"
        f"Output language: {LANGUAGE_NAMES[language]}\n"
        f"Today's date: {today.isoformat()}\n"
        f"Source type: {content.source_format.value}\n"
        f"Source URL: {url}\n"
        "</task>\n\n"
        "<source_document>\n"
        f"{title_line}{body}\n"
        "</source_document>"
    )


class Summarizer:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        effort: Effort | None = None,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._client = client
        self._model = model
        self._today = today
        self._system = load_system_prompt()
        self._output_config: OutputConfigParam = {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(LLMSummary)}
        }
        if effort:
            self._output_config["effort"] = effort
        # Identifies everything that shapes an answer (model, effort, schema, prompt): a stored
        # answer made under another fingerprint is not served.
        settings_json = json.dumps(self._output_config, sort_keys=True)
        material = f"{model}\n{settings_json}\n{self._system}"
        self.fingerprint = hashlib.sha256(material.encode()).hexdigest()[:16]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Summarizer":
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), timeout=LLM_TIMEOUT_S
        )
        return cls(client, model=settings.llm_model, effort=settings.llm_effort)

    async def aclose(self) -> None:
        await self._client.close()

    async def summarize(
        self, content: ExtractedContent, *, language: str, source_url: str | None = None
    ) -> Summary:
        """Summarise `content` in `language`. `source_url` overrides the URL shown as the source
        (the link the user sent rather than the final one after redirects)."""
        if language not in LANGUAGE_NAMES:
            raise ValueError(f"unsupported language: {language!r}")
        today = self._today()
        started = time.monotonic()

        user_message = build_user_message(content, language=language, today=today)
        messages: list[MessageParam] = [{"role": "user", "content": user_message}]
        raw, llm_summary, problem = await self._ask(messages)
        if llm_summary is None:
            # One retry, showing the model what was wrong with its answer. A new list: the one
            # already sent must stay as it was.
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"That JSON failed validation: {problem}. Return the corrected JSON only."
                    ),
                },
            ]
            raw, llm_summary, problem = await self._ask(messages)
            if llm_summary is None:
                logger.warning("LLM answer invalid twice: %s", problem)
                raise LLMError("llm_invalid")

        summary = assemble_summary(
            llm_summary,
            content,
            language=language,
            today=today,
            source_url=source_url or content.source_url,
        )
        logger.info(
            "Summarised: class=%s format=%s lang=%s deadlines=%d (verified %d) %.1fs",
            summary.source_type.value,
            content.source_format.value,
            language,
            len(summary.deadlines),
            sum(d.verified for d in summary.deadlines),
            time.monotonic() - started,
        )
        return summary

    async def _ask(self, messages: list[MessageParam]) -> tuple[str, LLMSummary | None, str]:
        """One request. Returns (raw text, parsed answer, problem). `problem` describes why the
        answer failed validation (then the parsed answer is None). Nothing is stored on `self`:
        one Summarizer serves every user at once."""
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=self._system,
                messages=messages,
                output_config=self._output_config,
            )
        except anthropic.APIError as exc:
            raise _map_api_error(exc) from exc

        usage = response.usage
        logger.info(
            "LLM call: model=%s in=%d out=%d stop=%s",
            self._model,
            usage.input_tokens,
            usage.output_tokens,
            response.stop_reason,
        )
        if response.stop_reason == "refusal":
            raise LLMError("llm_refused")
        if response.stop_reason == "max_tokens":
            raise LLMError("llm_truncated")

        raw = next((block.text for block in response.content if block.type == "text"), "")
        try:
            return raw, LLMSummary.model_validate_json(raw), ""
        except ValidationError as exc:
            # Only locations and messages: the error's `input` would echo page content.
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
                for err in exc.errors()[:_MAX_ERROR_DETAILS]
            )
            return raw, None, details or "not valid JSON"


def assemble_summary(
    llm: LLMSummary,
    content: ExtractedContent,
    *,
    language: str,
    today: date,
    source_url: str,
) -> Summary:
    """Turn the model's answer into a `Summary`, with every claim checked against the text."""
    deadlines = verify_deadlines(llm.deadlines, content.text, today)
    links, dropped = filter_links(llm.useful_links, content.text, content.links)

    summary = Summary(
        source_url=source_url,
        source_format=content.source_format,
        source_type=llm.source_type,
        language=language,
        title=(llm.title or content.title or None),
        organization=llm.organization,
        overview=llm.overview.strip(),
        deadlines=deadlines,
        key_points=[item.strip() for item in llm.key_points if item.strip()],
        eligibility=[item.strip() for item in llm.eligibility if item.strip()],
        costs=llm.costs,
        requirements=[item.strip() for item in llm.requirements if item.strip()],
        useful_links=links,
        warnings=[clean for item in llm.warnings if (clean := strip_urls(item).strip())],
        confidence=llm.confidence,
        dropped_links=dropped,
        truncated=content.meta.get("truncated") == "true",
        pages_read=_as_int(content.meta.get("pages_read")),
        pages_total=_as_int(content.meta.get("pages_total")),
    )
    return summary.model_copy(update={"confidence": _calibrated(summary)})


def _calibrated(summary: Summary) -> Confidence:
    """The model's confidence, lowered where the code knows better."""
    confidence = summary.confidence
    if summary.partial_coverage and confidence.rank > Confidence.MEDIUM.rank:
        confidence = Confidence.MEDIUM  # part of the source was never read
    if summary.deadlines and not any(d.verified for d in summary.deadlines):
        confidence = Confidence.LOW  # not one deadline could be tied to the text
    return confidence


def _as_int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _map_api_error(exc: anthropic.APIError) -> LLMError:
    """Stable reason codes for the user message. Details go to the log, never to the user."""
    if isinstance(exc, anthropic.RateLimitError | anthropic.OverloadedError):
        reason = "llm_busy"
    elif isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        reason = "llm_config"
    elif isinstance(exc, anthropic.NotFoundError):
        reason = "llm_config"  # unknown model name
    elif isinstance(exc, anthropic.RequestTooLargeError):
        reason = "llm_too_large"
    elif isinstance(exc, anthropic.BadRequestError):
        too_long = "too long" in str(exc).lower() or "too many tokens" in str(exc).lower()
        reason = "llm_too_large" if too_long else "llm_failed"
    elif isinstance(exc, anthropic.APIConnectionError):  # includes timeouts
        reason = "llm_unreachable"
    elif isinstance(exc, anthropic.APIStatusError) and exc.status_code in (503, 529):
        reason = "llm_busy"
    else:
        reason = "llm_failed"

    level = logging.ERROR if reason in {"llm_config", "llm_failed"} else logging.WARNING
    logger.log(level, "Anthropic API error: %s (%s)", type(exc).__name__, reason)
    return LLMError(reason)
