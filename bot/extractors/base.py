"""The contract every extractor fulfils: raw source in, `ExtractedContent` out.

The orchestrator (task 1.29) picks an extractor by `SourceFormat` and knows nothing else
about the formats.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from bot.pipeline.detector import SourceFormat

MAX_LINKS = 100

_URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?»”…"


@dataclass(frozen=True)
class RawSource:
    """What an extractor receives. `body` is empty for sources fetched by the extractor itself
    (YouTube). `url` is the final URL after redirects: the base for relative links."""

    url: str
    body: bytes = b""
    content_type: str = ""


@dataclass(frozen=True)
class ExtractedContent:
    text: str
    title: str | None
    source_url: str  # the URL this content came from (final URL, after redirects)
    source_format: SourceFormat
    # Absolute http(s) URLs that actually occur in the source. The summary may only offer
    # links from this set (anti-hallucination rule in ARCHITECTURE.md).
    links: tuple[str, ...] = ()
    # Free-form facts about the source: "published", "site_name", "pages_total", "kind" ...
    meta: dict[str, str] = field(default_factory=dict)


class Extractor(Protocol):
    async def extract(self, source: RawSource) -> ExtractedContent: ...


def find_urls(text: str) -> tuple[str, ...]:
    """Unique http(s) URLs in `text`, in order of appearance, capped at MAX_LINKS."""
    seen: dict[str, None] = {}
    for match in _URL_RE.finditer(text):
        seen.setdefault(match.group().rstrip(_TRAILING_PUNCT), None)
        if len(seen) >= MAX_LINKS:
            break
    return tuple(seen)


def clean_meta(**values: str | None) -> dict[str, str]:
    """Drop empty values so `meta` only holds facts we actually have."""
    return {key: value.strip() for key, value in values.items() if value and value.strip()}
