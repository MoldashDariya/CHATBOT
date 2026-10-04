"""Reading the pages a page points to (depth one), when it leaves the question unanswered.

A programme page often says "dates and requirements are on the application page". If the
summary has no deadline backed by the text, the pages the user's page links to for exactly that
purpose are read too. Strict limits keep this safe and cheap:

- only pages of the same site (same registrable domain), never anywhere the page happens to link;
- only links that look like admission pages (words such as "apply", "deadline", "requirements"
  in the link text or the address), best matches first, at most a few;
- one level only: linked pages are never followed further;
- the fetch goes through the same SSRF-guarded `SafeFetcher` as the user's own link.
"""

import re
from collections.abc import Sequence
from dataclasses import replace
from urllib.parse import unquote, urlsplit

from bot.extractors.base import ExtractedContent

# Substrings, lower case. Admission pages say this in English, German, Russian and Kazakh.
_ADMISSION_WORDS = (
    "apply", "application", "admission", "enrol", "enroll", "deadline", "requirement",
    "entry", "dates", "bewerb", "zulassung", "anmeld", "frist", "voraussetzung",
    "поступл", "приём", "прием", "подач", "заявк", "срок", "документ", "требован", "абитуриент",
    "қабылда", "құжат", "талап", "өтініш",
)  # fmt: skip
_SKIPPED_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "gif", "svg", "webp", "ico", "zip", "rar", "gz", "7z", "tar", "mp3",
     "mp4", "avi", "mov", "css", "js", "xml", "json", "exe", "dmg", "apk", "xls", "xlsx", "ppt",
     "pptx", "doc"}
)  # fmt: skip
_SECOND_LEVEL_LABELS = frozenset({"co", "com", "org", "net", "ac", "edu", "gov"})
_WORD_RE = re.compile(r"[^\W\d_]+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_LINKED_PAGE_MARKER = "=== Linked page: {url} ==="


def registrable_domain(host: str) -> str:
    """The part of a host name that identifies the site ("sc.informatik.rwth-aachen.de" ->
    "rwth-aachen.de", "www.ox.ac.uk" -> "ox.ac.uk"). Not the public-suffix list, a close fit."""
    host = host.lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2 or all(label.isdigit() for label in labels):
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_LABELS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(url: str, other: str) -> bool:
    host, other_host = urlsplit(url).hostname, urlsplit(other).hostname
    return bool(host and other_host and registrable_domain(host) == registrable_domain(other_host))


def candidate_links(content: ExtractedContent, *, limit: int) -> list[str]:
    """Links of `content` worth reading next, best first. Empty if `limit` is zero."""
    if limit <= 0:
        return []
    anchors = _anchor_texts(content.text)
    own = _page_identity(content.source_url)

    scored: dict[str, tuple[int, int]] = {}  # identity -> (score, position)
    urls: dict[str, str] = {}
    for position, url in enumerate(content.links):
        identity = _page_identity(url)
        if identity == own or identity in scored or not same_site(url, content.source_url):
            continue
        if _extension(url) in _SKIPPED_EXTENSIONS:
            continue
        score = _score(url, anchors.get(url, ()))
        if score > 0:
            scored[identity] = (score, position)
            urls[identity] = url

    ranked = sorted(scored, key=lambda identity: (-scored[identity][0], scored[identity][1]))
    return [urls[identity] for identity in ranked[:limit]]


def combine(main: ExtractedContent, extras: Sequence[ExtractedContent]) -> ExtractedContent:
    """The main page followed by each linked page under its own marker line."""
    parts = [main.text]
    links = dict.fromkeys(main.links)
    for extra in extras:
        parts.append(f"{_LINKED_PAGE_MARKER.format(url=extra.source_url)}\n{extra.text}")
        links.update(dict.fromkeys(extra.links))
    meta = {**main.meta, "followed_pages": str(len(extras))}
    return replace(main, text="\n\n".join(parts), links=tuple(links), meta=meta)


def _score(url: str, anchor_texts: Sequence[str]) -> int:
    """Admission words in the address count once each, in the link text twice each."""
    score = _matches(unquote(urlsplit(url).path))
    for text in anchor_texts:
        score += 2 * _matches(text)
    return score


def _matches(text: str) -> int:
    """How many of the words in `text` start with an admission word. Matching the start of a
    word, not any substring, keeps "updates" and "candidates" from counting as "dates"."""
    words = _WORD_RE.findall(text.lower())
    return sum(any(word.startswith(keyword) for keyword in _ADMISSION_WORDS) for word in words)


def _anchor_texts(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for label, url in _MARKDOWN_LINK_RE.findall(text):
        found.setdefault(url, []).append(label)
    return found


def _page_identity(url: str) -> str:
    """The page, ignoring scheme, "www.", a trailing slash and the fragment."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}?{parts.query}"


def _extension(url: str) -> str:
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[-1].lower() if "." in last else ""
