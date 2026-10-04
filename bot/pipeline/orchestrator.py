"""The whole pipeline: detect -> fetch -> extract -> clean -> summarise.

`run(url)` is what the bot calls. `process(...)` is the part after the download, so tests and
the evaluation tool can feed it saved files without a network.

Everything that can go wrong raises a `BotError` subclass (see `bot/errors.py`); turning it into
a message is the caller's job (`render/errors.py`). The result is a `Summary`; rendering it is
the caller's job too.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum

from bot.config import Settings
from bot.errors import ContentTooLargeError, ProcessingTimeoutError
from bot.extractors import build_extractors
from bot.extractors.base import Extractor, RawSource
from bot.pipeline.cleaner import clean_content, ensure_enough_text
from bot.pipeline.detector import (
    SourceFormat,
    check_filename,
    detect_from_content,
    detect_from_url,
    detect_response,
)
from bot.pipeline.schema import Summary
from bot.pipeline.summarizer import Summarizer
from bot.security.safe_fetch import SafeFetcher

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 240.0
# What the model and the extractors see as the "address" of an uploaded file. Fixed on purpose:
# the real file name is chosen by the sender and must not reach the prompt.
FILE_PLACEHOLDER_URL = "telegram://uploaded-file"


class Stage(StrEnum):
    """Coarse progress, for the "reading..." message the user watches."""

    FETCHING = "fetching"
    READING = "reading"
    ANALYZING = "analyzing"


StageCallback = Callable[[Stage], Awaitable[None]]


class Orchestrator:
    def __init__(
        self,
        *,
        fetcher: SafeFetcher,
        extractors: Mapping[SourceFormat, Extractor],
        summarizer: Summarizer,
        max_chars: int,
        max_concurrent: int = 4,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_file_bytes: int = 15 * 1024 * 1024,
    ) -> None:
        self._fetcher = fetcher
        self._extractors = extractors
        self._summarizer = summarizer
        self._max_chars = max_chars
        self._timeout_s = timeout_s
        self._max_file_bytes = max_file_bytes
        # Parsing runs in threads and the model call is the expensive part: cap both so a burst
        # of users queues up instead of exhausting CPU, memory or the API rate limit.
        self._slots = asyncio.Semaphore(max_concurrent)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Orchestrator":
        fetcher = SafeFetcher.from_settings(settings)
        return cls(
            fetcher=fetcher,
            extractors=build_extractors(settings, fetcher),
            summarizer=Summarizer.from_settings(settings),
            max_chars=settings.max_chars,
            max_concurrent=settings.max_concurrent_jobs,
            max_file_bytes=settings.max_download_mb * 1024 * 1024,
        )

    async def aclose(self) -> None:
        await self._summarizer.aclose()

    async def run(
        self, url: str, *, language: str, on_stage: StageCallback | None = None
    ) -> Summary:
        """Summarise whatever `url` points to. The time budget covers waiting for a free slot."""
        timeout = asyncio.timeout(self._timeout_s)
        try:
            async with timeout, self._slots:
                return await self._run(url, language, on_stage)
        except TimeoutError as exc:
            if timeout.expired():
                raise ProcessingTimeoutError(f"over {self._timeout_s}s") from exc
            raise

    async def run_file(
        self,
        body: bytes,
        *,
        filename: str,
        content_type: str = "",
        language: str,
        display_name: str | None = None,
        on_stage: StageCallback | None = None,
    ) -> Summary:
        """Summarise a file the user sent (already downloaded). Same time budget and slots as
        `run`. `display_name` is shown as the source; it is never given to the model, because a
        file name is text the sender chose."""
        timeout = asyncio.timeout(self._timeout_s)
        try:
            async with timeout, self._slots:
                check_filename(filename)
                if len(body) > self._max_file_bytes:
                    raise ContentTooLargeError(f"{len(body)} bytes")
                source_format = detect_from_content(
                    content_type.split(";")[0].strip().lower(), body
                )
                return await self.process(
                    RawSource(url=FILE_PLACEHOLDER_URL, body=body, content_type=content_type),
                    source_format,
                    language=language,
                    source_url=display_name or filename,
                    on_stage=on_stage,
                )
        except TimeoutError as exc:
            if timeout.expired():
                raise ProcessingTimeoutError(f"over {self._timeout_s}s") from exc
            raise

    async def _run(self, url: str, language: str, on_stage: StageCallback | None) -> Summary:
        source_format = detect_from_url(url)  # YouTube videos and legacy .doc, no download
        if source_format is None:
            await _report(on_stage, Stage.FETCHING)
            fetched = await self._fetcher.fetch(url)
            source_format = detect_response(fetched)  # the bytes decide, and the final URL
            raw = RawSource(url=fetched.url, body=fetched.body, content_type=fetched.content_type)
        else:
            raw = RawSource(url=url)
        return await self.process(
            raw, source_format, language=language, source_url=url, on_stage=on_stage
        )

    async def process(
        self,
        raw: RawSource,
        source_format: SourceFormat,
        *,
        language: str,
        source_url: str | None = None,
        on_stage: StageCallback | None = None,
    ) -> Summary:
        """Everything after the download. `source_url` is shown as the source; by default the
        user's own link (not the final URL after redirects, which may be a tracking hop)."""
        await _report(on_stage, Stage.READING)
        content = await self._extractors[source_format].extract(raw)
        content = clean_content(content, self._max_chars)
        ensure_enough_text(content)  # a near-empty page never reaches the model

        await _report(on_stage, Stage.ANALYZING)
        return await self._summarizer.summarize(
            content, language=language, source_url=source_url or raw.url
        )


async def _report(on_stage: StageCallback | None, stage: Stage) -> None:
    """Progress is cosmetic: a failing callback must never fail the request."""
    if on_stage is None:
        return
    try:
        await on_stage(stage)
    except Exception:
        logger.warning("Stage callback failed at %s", stage.value, exc_info=True)
