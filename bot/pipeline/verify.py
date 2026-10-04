"""Checks that hold the model's answer to the source text (anti-hallucination, ARCHITECTURE.md).

The model may only report what the source says. Deadlines must come with a verbatim quote and
the date as written; links must occur in the source. Everything is judged against the cleaned
text the model actually saw.
"""

import re
import unicodedata
from datetime import date
from urllib.parse import urlsplit

from bot.extractors.base import find_urls
from bot.pipeline.schema import Deadline, LLMDeadline, LLMLink

MIN_QUOTE_CHARS = 8
MAX_LINKS = 8

# Typographic variants that the model or the source may use interchangeably.
_PUNCTUATION = str.maketrans(
    {
        "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
        "‘": "'", "’": "'", "‚": "'", "‹": "'", "›": "'", "`": "'",
        "–": "-", "—": "-", "−": "-", "‒": "-", "‑": "-",
        "…": "...",
    }
)  # fmt: skip
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)\s]*\)")
_MARKUP_RE = re.compile(r"[|*#_>]")  # table pipes, emphasis, heading and quote markers
_ELLIPSIS_RE = re.compile(r"\.{3}")


def normalize(text: str) -> str:
    """Comparison form: markdown/table markup removed, one kind of quote and dash, no case,
    single spaces. Applied to both the source and the quote, so only real differences remain."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKUP_RE.sub(" ", text)
    return " ".join(text.casefold().split())


def occurs(snippet: str, normalized_source: str) -> bool:
    """True if `snippet` (not yet normalised) is in the source. A quote may elide with "...":
    then each fragment must occur, in order."""
    needle = normalize(snippet)
    if len(needle) < MIN_QUOTE_CHARS:
        return False
    position = 0
    for fragment in _ELLIPSIS_RE.split(needle):
        fragment = fragment.strip(" .")
        if not fragment:
            continue
        found = normalized_source.find(fragment, position)
        if found == -1:
            return False
        position = found + len(fragment)
    return True


_URL_IN_TEXT_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]]+", re.IGNORECASE)


def strip_urls(text: str) -> str:
    """Remove URLs from model-written prose. Telegram turns any URL into a tappable link, so a
    warning that quotes an attacker's address (while describing an injection attempt, say) would
    hand the user that address. Links are offered only through `useful_links`, which is
    checked against the source."""
    return " ".join(_URL_IN_TEXT_RE.sub("…", text).split())


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def verify_deadlines(deadlines: list[LLMDeadline], source_text: str, today: date) -> list[Deadline]:
    """Attach `verified` / `possibly_passed` to each deadline.

    verified: the quote occurs in the source AND the date as written occurs in the source.
    A full ISO date is kept only if its year appears in the date text or the quote: a year the
    source does not state is a guess, and a guessed year must not drive "may have passed".
    """
    source = normalize(source_text)
    checked: list[Deadline] = []
    for item in deadlines:
        date_iso = parse_iso_date(item.date_iso)
        evidence = normalize(f"{item.date_text} {item.quote}")
        if date_iso is not None and str(date_iso.year) not in evidence:
            date_iso = None

        date_in_source = len(normalize(item.date_text)) >= 2 and (
            normalize(item.date_text) in source
        )
        verified = occurs(item.quote, source) and date_in_source
        checked.append(
            Deadline(
                label=item.label.strip(),
                date_iso=date_iso,
                date_text=item.date_text.strip(),
                quote=item.quote.strip(),
                verified=verified,
                possibly_passed=date_iso is not None and date_iso < today,
            )
        )
    return checked


def filter_links(
    links: list[LLMLink], source_text: str, source_links: tuple[str, ...]
) -> tuple[list[LLMLink], int]:
    """Keep only links whose URL is in the source (its link list or its text).

    Compared as whole URLs; a trailing slash does not matter. Returns (kept, dropped_count).
    The same page reached as `http://www.x.org/a` and `https://x.org/a/` is one link (the
    https one is kept); the result is capped at MAX_LINKS.
    """
    allowed = {_url_key(url) for url in (*source_links, *find_urls(source_text))}
    kept: dict[str, LLMLink] = {}
    dropped = 0
    for link in links:
        key = _url_key(link.url)
        if not link.url.lower().startswith(("http://", "https://")) or key not in allowed:
            dropped += 1
            continue
        same_page = _page_key(link.url)
        earlier = kept.get(same_page)
        if earlier is None:
            kept[same_page] = LLMLink(title=link.title.strip(), url=link.url.strip())
        elif link.url.lower().startswith("https://") and earlier.url.lower().startswith("http://"):
            kept[same_page] = LLMLink(title=earlier.title, url=link.url.strip())
    return list(kept.values())[:MAX_LINKS], dropped


def _url_key(url: str) -> str:
    return url.strip().rstrip("/")


def _page_key(url: str) -> str:
    """Identity of a page regardless of scheme, a leading "www." and a trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}?{parts.query}"
