import asyncio
import html
import json
import logging
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol
from urllib.parse import urlencode

import requests
from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)

from bot.errors import BotError, ExtractionError
from bot.extractors.base import ExtractedContent, RawSource, clean_meta, find_urls
from bot.pipeline.detector import SourceFormat
from bot.security.safe_fetch import SafeFetcher
from bot.urls import youtube_video_id

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGES = ("ru", "kk", "en")
DEFAULT_TIMEOUT_S = 30.0
_HTTP_TIMEOUT = (5, 15)  # (connect, read) seconds for every request the library makes
_MAX_DESCRIPTION_CHARS = 5000

_DESCRIPTION_RE = re.compile(r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.)*)"')
# Sound cues such as "[Music]", "[Музыка]", "[♪♪♪]"; brackets holding digits are kept ("[1]").
_CUE_RE = re.compile(r"\[[^\]\d]{1,40}\]")
_SPEAKER_CHANGE_RE = re.compile(r"(?:^|\s)>>\s*")

_NO_TRANSCRIPT = (TranscriptsDisabled, NoTranscriptFound)
_UNAVAILABLE = (VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId)
_BLOCKED = (IpBlocked, RequestBlocked)


class _TimeoutSession(requests.Session):
    """The library offers no timeout setting; without one a stalled request would hold its
    worker thread forever."""

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        return super().request(*args, **kwargs)


class _TranscriptLike(Protocol):
    language_code: str
    is_generated: bool

    def fetch(self) -> Any: ...


def pick_transcript[T: _TranscriptLike](
    transcripts: Sequence[T], preferred: Iterable[str]
) -> T | None:
    """Best available transcript.

    Order: manual in a preferred language, automatic in a preferred language, any manual,
    any automatic; among preferred languages, the caller's order. Region variants count as
    their base language ("de-DE" is "de"). Automatic captions are the spoken language, so
    "automatic + preferred" means the video itself is in a language we expect.
    """
    wanted = [lang.lower() for lang in preferred]

    def rank(transcript: T) -> tuple[int, int]:
        base = transcript.language_code.lower().split("-")[0]
        in_preferred = base in wanted
        tier = (1 if transcript.is_generated else 0) + (0 if in_preferred else 2)
        return tier, wanted.index(base) if in_preferred else 0

    return min(transcripts, key=rank, default=None)


def clean_snippet(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ").replace("♪", " ")
    text = _CUE_RE.sub(" ", text)
    text = _SPEAKER_CHANGE_RE.sub(" ", text)
    return " ".join(text.split())


def parse_short_description(page: str) -> str:
    """The full description embedded in a watch page's player data; empty if not found."""
    match = _DESCRIPTION_RE.search(page)
    if not match:
        return ""
    try:
        return str(json.loads(f'"{match.group(1)}"')).strip()[:_MAX_DESCRIPTION_CHARS]
    except ValueError:
        return ""


class YouTubeExtractor:
    """Title, channel, description and the video's captions, labelled as a video (not a
    document). Captions come from `youtube-transcript-api` in a worker thread; title and
    description are best effort and never fail the extraction."""

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        api_factory: Callable[[], YouTubeTranscriptApi] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._languages = tuple(languages)
        self._timeout_s = timeout_s
        self._api_factory = api_factory or (
            lambda: YouTubeTranscriptApi(http_client=_TimeoutSession())
        )

    async def extract(self, source: RawSource) -> ExtractedContent:
        video_id = youtube_video_id(source.url)
        if video_id is None:
            raise ExtractionError("video_unavailable")

        info_task = asyncio.create_task(self._video_info(video_id))
        try:
            language, generated, snippets = await asyncio.wait_for(
                asyncio.to_thread(self._fetch_captions, video_id), self._timeout_s
            )
            title, channel, description = await info_task
        except TimeoutError as exc:
            raise ExtractionError("timeout") from exc
        finally:
            info_task.cancel()

        transcript = " ".join(filter(None, (clean_snippet(s) for s in snippets)))
        if not transcript:
            raise ExtractionError("no_transcript")

        kind = "auto-generated" if generated else "manual"
        sections = [
            f"YouTube video: {title}" if title else "YouTube video",
            f"Channel: {channel}" if channel else "",
            f"Description:\n{description}" if description else "",
            f"Transcript ({language}, {kind} captions):\n{transcript}",
        ]
        return ExtractedContent(
            text="\n\n".join(section for section in sections if section),
            title=title,
            source_url=source.url,
            source_format=SourceFormat.YOUTUBE,
            links=find_urls(description),
            meta=clean_meta(
                kind="video",
                video_id=video_id,
                channel=channel,
                transcript_language=language,
                transcript_kind=kind,
            ),
        )

    def _fetch_captions(self, video_id: str) -> tuple[str, bool, list[str]]:
        try:
            transcripts = list(self._api_factory().list(video_id))
            chosen = pick_transcript(transcripts, self._languages)
            if chosen is None:
                raise ExtractionError("no_transcript")
            fetched = chosen.fetch()
            return chosen.language_code, chosen.is_generated, [s.text for s in fetched.snippets]
        except ExtractionError:
            raise
        except _NO_TRANSCRIPT as exc:
            raise ExtractionError("no_transcript") from exc
        except _UNAVAILABLE as exc:
            raise ExtractionError("video_unavailable") from exc
        except _BLOCKED as exc:
            raise ExtractionError("youtube_blocked") from exc
        except Exception as exc:
            logger.warning("YouTube captions failed: %s", type(exc).__name__, exc_info=True)
            raise ExtractionError("youtube_failed") from exc

    async def _video_info(self, video_id: str) -> tuple[str | None, str | None, str]:
        """(title, channel, description). Best effort: every part is optional and nothing in
        here may fail the extraction."""
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        oembed_url = "https://www.youtube.com/oembed?" + urlencode(
            {"url": watch_url, "format": "json"}
        )
        try:
            oembed, page = await asyncio.gather(
                self._get_text(oembed_url), self._get_text(watch_url)
            )
            data = json.loads(oembed) if oembed else {}
            title = str(data.get("title") or "").strip() or None
            channel = str(data.get("author_name") or "").strip() or None
            return title, channel, parse_short_description(page)
        except (ValueError, AttributeError, TypeError):
            logger.debug("Unusable YouTube video info", exc_info=True)
            return None, None, ""

    async def _get_text(self, url: str) -> str:
        try:
            result = await self._fetcher.fetch(url)
        except BotError:
            return ""
        return result.body.decode("utf-8", errors="ignore")
