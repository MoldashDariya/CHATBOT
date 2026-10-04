"""The whole pipeline: detect -> fetch -> extract -> clean -> summarise.

`run(url)` is what the bot calls. `process(...)` is the part after the download, so tests and
the evaluation tool can feed it saved files without a network.

Everything that can go wrong raises a `BotError` subclass (see `bot/errors.py`); turning it into
a message is the caller's job (`render/errors.py`). The result is a `Summary`; rendering it is
the caller's job too.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum

from bot.config import Settings
from bot.errors import BotError, ContentTooLargeError, ProcessingTimeoutError
from bot.extractors import build_extractors
from bot.extractors.base import ExtractedContent, Extractor, RawSource
from bot.pipeline.cleaner import clean_content, ensure_enough_text
from bot.pipeline.detector import (
    SourceFormat,
    check_filename,
    detect_from_content,
    detect_from_url,
    detect_response,
)
from bot.pipeline.followups import candidate_links, combine
from bot.pipeline.schema import SourceClass, Summary
from bot.pipeline.summarizer import Summarizer
from bot.security.safe_fetch import SafeFetcher
from bot.storage.cache import CacheEntry, SummaryCache
from bot.urls import cache_key

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 240.0
# What the model and the extractors see as the "address" of an uploaded file. Fixed on purpose:
# the real file name is chosen by the sender and must not reach the prompt.
FILE_PLACEHOLDER_URL = "telegram://uploaded-file"
# Bump when extraction, cleaning, verification or the card change what an answer contains: stored
# answers made before the change are then no longer served. (Model, effort, prompt and schema are
# covered by the summarizer's own fingerprint.)
PIPELINE_VERSION = 1


class Stage(StrEnum):
    """Coarse progress, for the "reading..." message the user watches."""

    FETCHING = "fetching"
    READING = "reading"
    ANALYZING = "analyzing"
    FOLLOWING = "following"  # reading pages the page links to


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
        follow_pages: int = 0,
        cache: SummaryCache | None = None,
        cache_ttl_s: float = 0.0,
        clock: Callable[[], float] = time.time,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._fetcher = fetcher
        self._extractors = extractors
        self._summarizer = summarizer
        self._max_chars = max_chars
        self._timeout_s = timeout_s
        self._max_file_bytes = max_file_bytes
        self._follow_pages = follow_pages
        # The answer cache is on only with a store and a positive freshness time.
        self._cache = cache if cache_ttl_s > 0 else None
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._today = today
        self._fingerprint = hashlib.sha256(
            f"{PIPELINE_VERSION}|{summarizer.fingerprint}|follow={follow_pages}|chars={max_chars}".encode()
        ).hexdigest()[:16]
        # Parsing runs in threads and the model call is the expensive part: cap both so a burst
        # of users queues up instead of exhausting CPU, memory or the API rate limit.
        self._slots = asyncio.Semaphore(max_concurrent)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Orchestrator":
        fetcher = SafeFetcher.from_settings(settings)
        cache = SummaryCache(settings.cache_db_path) if settings.cache_ttl_hours > 0 else None
        return cls(
            fetcher=fetcher,
            extractors=build_extractors(settings, fetcher),
            summarizer=Summarizer.from_settings(settings),
            max_chars=settings.max_chars,
            max_concurrent=settings.max_concurrent_jobs,
            max_file_bytes=settings.max_download_mb * 1024 * 1024,
            follow_pages=settings.follow_linked_pages,
            cache=cache,
            cache_ttl_s=settings.cache_ttl_hours * 3600,
        )

    async def aclose(self) -> None:
        await self._summarizer.aclose()
        if self._cache is not None:
            await self._cache.aclose()

    async def cached_summary(self, url: str, language: str) -> Summary | None:
        """A stored answer for `url` that is still fresh, ready to show: no download, no model
        call, no waiting for a slot. None if there is none (or the cache is off)."""
        entry = await self._lookup(url, language)
        return self._serve(entry, url) if entry and self._is_fresh(entry) else None

    async def refresh_button_id(self, url: str, language: str) -> str | None:
        """The id for the "refresh" button under the answer for `url`, if that answer is stored."""
        entry = await self._lookup(url, language)
        return entry.button_id if entry else None

    async def refresh_target(self, button_id: str) -> tuple[str, str] | None:
        """(link, language) a refresh button stands for, or None if it has expired."""
        return await self._cache.target_for(button_id) if self._cache else None

    async def run(
        self,
        url: str,
        *,
        language: str,
        on_stage: StageCallback | None = None,
        refresh: bool = False,
    ) -> Summary:
        """Summarise whatever `url` points to. The time budget covers waiting for a free slot.
        A fresh stored answer is returned as it is; `refresh` ignores whatever is stored."""
        stored = None if refresh else await self._lookup(url, language)
        if stored is not None and self._is_fresh(stored):
            return self._serve(stored, url)

        timeout = asyncio.timeout(self._timeout_s)
        try:
            async with timeout, self._slots:
                return await self._run(url, language, on_stage, stored)
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

    async def _run(
        self, url: str, language: str, on_stage: StageCallback | None, stored: CacheEntry | None
    ) -> Summary:
        source_format = detect_from_url(url)  # YouTube videos and legacy .doc, no download
        if source_format is None:
            await _report(on_stage, Stage.FETCHING)
            fetched = await self._fetcher.fetch(url)
            source_format = detect_response(fetched)  # the bytes decide, and the final URL
            raw = RawSource(url=fetched.url, body=fetched.body, content_type=fetched.content_type)
        else:
            raw = RawSource(url=url)

        content = await self._read(raw, source_format, on_stage)
        content_hash = _text_hash(content.text)
        if stored is not None and stored.content_hash == content_hash:
            # Past its freshness time but the page reads exactly as before: the model would be
            # asked the same question again. The age shown stays that of the stored answer.
            logger.info("Cache: page unchanged, stored answer reused")
            return self._serve(stored, url)

        summary = await self._summarize(
            content, source_format, language=language, shown=url, on_stage=on_stage, follow=True
        )
        await self._store(url, language, content_hash, summary)
        return summary

    async def process(
        self,
        raw: RawSource,
        source_format: SourceFormat,
        *,
        language: str,
        source_url: str | None = None,
        on_stage: StageCallback | None = None,
        follow: bool = False,
    ) -> Summary:
        """Everything after the download, without the cache. `source_url` is shown as the
        source; by default the user's own link (not the final URL after redirects, which may be a
        tracking hop). `follow` allows reading linked pages when the summary has no confirmed
        deadline; only `run` turns it on, never an uploaded file or a saved evaluation input."""
        content = await self._read(raw, source_format, on_stage)
        return await self._summarize(
            content,
            source_format,
            language=language,
            shown=source_url or raw.url,
            on_stage=on_stage,
            follow=follow,
        )

    async def _read(
        self, raw: RawSource, source_format: SourceFormat, on_stage: StageCallback | None
    ) -> ExtractedContent:
        await _report(on_stage, Stage.READING)
        content = await self._extractors[source_format].extract(raw)
        content = clean_content(content, self._max_chars)
        ensure_enough_text(content)  # a near-empty page never reaches the model
        return content

    async def _summarize(
        self,
        content: ExtractedContent,
        source_format: SourceFormat,
        *,
        language: str,
        shown: str,
        on_stage: StageCallback | None,
        follow: bool,
    ) -> Summary:
        await _report(on_stage, Stage.ANALYZING)
        summary = await self._summarizer.summarize(content, language=language, source_url=shown)

        if follow and source_format is SourceFormat.HTML and _needs_linked_pages(summary):
            extras = await self._read_linked_pages(content, on_stage)
            if extras:
                combined = clean_content(combine(content, extras), self._max_chars)
                await _report(on_stage, Stage.ANALYZING)
                try:
                    fuller = await self._summarizer.summarize(
                        combined, language=language, source_url=shown
                    )
                except BotError as exc:
                    # The first answer is already good enough to show; the bonus must not cost it.
                    logger.info("Second pass skipped: %s", type(exc).__name__)
                else:
                    if _confirmed_deadlines(fuller) > _confirmed_deadlines(summary):
                        summary = fuller.model_copy(
                            update={"extra_sources": [extra.source_url for extra in extras]}
                        )
        return summary

    # --- the answer cache -----------------------------------------------------------------

    async def _lookup(self, url: str, language: str) -> CacheEntry | None:
        key = cache_key(url) if self._cache else None
        if self._cache is None or key is None:
            return None
        return await self._cache.get(key, language, self._fingerprint)

    async def _store(self, url: str, language: str, content_hash: str, summary: Summary) -> None:
        key = cache_key(url) if self._cache else None
        if self._cache is None or key is None:
            return
        await self._cache.put(
            key,
            language,
            url=url,
            fingerprint=self._fingerprint,
            content_hash=content_hash,
            summary=summary,
        )

    def _is_fresh(self, entry: CacheEntry) -> bool:
        return self._clock() - entry.stored_at < self._cache_ttl_s

    def _serve(self, entry: CacheEntry, url: str) -> Summary:
        """A stored answer as it is shown now: the source is the link just sent (the stored one
        may differ in tracking parameters), "may have passed" is worked out against today, and
        the age is attached so the card can say that it is not freshly read."""
        today = self._today()
        deadlines = [
            deadline.model_copy(
                update={
                    "possibly_passed": deadline.date_iso is not None and deadline.date_iso < today
                }
            )
            for deadline in entry.summary.deadlines
        ]
        age_minutes = max(0, int((self._clock() - entry.stored_at) // 60))
        return entry.summary.model_copy(
            update={"source_url": url, "deadlines": deadlines, "cached_minutes_ago": age_minutes}
        )

    async def _read_linked_pages(
        self, content: ExtractedContent, on_stage: StageCallback | None
    ) -> list[ExtractedContent]:
        """The pages the content links to for admission information, extracted and cleaned.
        A page that cannot be fetched or read is skipped: this is a bonus, never a failure."""
        urls = candidate_links(content, limit=self._follow_pages)
        if not urls:
            return []
        await _report(on_stage, Stage.FOLLOWING)
        results = await asyncio.gather(*(self._read_one(url) for url in urls))
        return [page for page in results if page is not None]

    async def _read_one(self, url: str) -> ExtractedContent | None:
        try:
            fetched = await self._fetcher.fetch(url)  # same SSRF guard and limits as any link
            source_format = detect_response(fetched)
            page = await self._extractors[source_format].extract(
                RawSource(url=fetched.url, body=fetched.body, content_type=fetched.content_type)
            )
            page = clean_content(page, self._max_chars)
            ensure_enough_text(page)
        except BotError as exc:
            logger.info("Linked page skipped: %s", type(exc).__name__)
            return None
        return page


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _confirmed_deadlines(summary: Summary) -> int:
    return sum(deadline.verified for deadline in summary.deadlines)


def _needs_linked_pages(summary: Summary) -> bool:
    return summary.source_type is not SourceClass.OTHER and _confirmed_deadlines(summary) == 0


async def _report(on_stage: StageCallback | None, stage: Stage) -> None:
    """Progress is cosmetic: a failing callback must never fail the request."""
    if on_stage is None:
        return
    try:
        await on_stage(stage)
    except Exception:
        logger.warning("Stage callback failed at %s", stage.value, exc_info=True)
