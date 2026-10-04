"""Text clean-up between extraction and the LLM.

Safe for every format: invisible characters, odd spaces, whitespace runs, blank-line runs,
adjacent duplicate lines, the `MAX_CHARS` cap. Web-only noise rules (cookie banners, "Skip to
content", "[edit]" links, footnote markers, wrapped lines) run for HTML only: in a PDF a line
like "Menu" can be real content.

Nothing here may alter facts: no line joining by guesswork, no de-hyphenation, no removal of
repeated lines that could be parallel clauses (those are only dropped when adjacent, or, for
HTML, when they are long enough to be page-builder duplication).
"""

import re
import unicodedata
from dataclasses import replace

from bot.errors import TooLittleTextError
from bot.extractors.base import ExtractedContent
from bot.pipeline.detector import SourceFormat

MIN_TEXT_CHARS = 300  # below this the LLM is not called (ARCHITECTURE.md)
TRUNCATION_MARKER = "[… the source continues, but the text was cut at the processing limit …]"

_MIN_ADJACENT_DUPLICATE_CHARS = 10
_MIN_DISTANT_DUPLICATE_CHARS = 200  # HTML only
_TRUNCATE_AT_LINE_IF_KEEPS = 0.8  # cut at a line break only if that keeps 80% of the budget

# --- characters ---------------------------------------------------------------------------

# Invisible characters: zero-width space, soft hyphen, word joiner, BOM, direction marks and
# bidi controls. ZWNJ/ZWJ (U+200C/D) are kept: some scripts and emoji need them. Odd *spaces*
# (no-break, thin, hair, ideographic...) need no table: str.split() already treats them as
# whitespace when runs are collapsed.
_INVISIBLE = {0x200B, 0x2060, 0xFEFF, 0x00AD, 0x200E, 0x200F, *range(0x202A, 0x202F)}
_INVISIBLE |= set(range(0x2066, 0x206A))
_CHAR_TABLE = dict.fromkeys(_INVISIBLE)
_LINE_BREAKS = {
    "\r\n": "\n",
    "\r": "\n",
    "\x0c": "\n",
    "\u2028": "\n",
    "\u2029": "\n",
    "\x85": "\n",
}

# --- structure ----------------------------------------------------------------------------

_STRUCTURAL_RE = re.compile(r"^\s*(?:[|#>]|[-*•–]\s|\d{1,3}[.)]\s)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
# What may NOT directly follow a complete URL: more URL characters, or "." / ":" / "," / ";"
# followed by a letter or digit (a trailing full stop is punctuation, "example.edu.au" is not).
_URL_END = r"(?![A-Za-z0-9_\-/~%?#&=+@]|[.:,;][A-Za-z0-9])"
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)\s]*\)")

# --- web noise (HTML only) ----------------------------------------------------------------

# Whole-line UI labels. A lone "[Read more](https://...)" is not matched (the URL is part of the
# line) and so stays: it may be the only pointer to the details.
_UI_LABEL_RE = re.compile(
    r"^(?:"
    r"skip (?:to )?(?:main )?(?:content|contents|navigation)"
    r"|(?:back )?to top|top of page|вернуться наверх|наверх|вверх"
    r"|share(?: this)?(?: page| article| post)?|share on \w+|поделиться"
    r"|print(?: this page)?|распечатать|версия для печати|печать"
    r"|read more|learn more|more info(?:rmation)?|continue reading"
    r"|читать далее|подробнее|толығырақ"
    r"|subscribe(?: to (?:our )?newsletter)?|sign up for (?:our )?newsletter|подписаться"
    r"|menu|search|close|open menu|toggle navigation|меню|поиск|закрыть|мәзір|іздеу"
    r"|accept(?: all)?(?: cookies)?|i agree|got it|decline|принять(?: все)?|согласен|отклонить"
    r")$",
    re.IGNORECASE,
)
# Sentences from banners and footers. Matched against the line with link markup removed.
_PHRASE_NOISE_RES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r".{0,200}\b(?:we|this (?:web)?site|our (?:web)?site) uses? cookies\b.{0,200}",
        r".{0,200}\b(?:accept|allow|agree to)(?: all)? cookies\b.{0,200}",
        r".{0,200}(?:мы используем|сайт использует|этот сайт использует)(?: файлы)? cookie.{0,200}",
        r".{0,200}\ball rights reserved\b.{0,50}",
        r"(?:©|\(c\)|copyright)\s*(?:©\s*)?\d{4}.{0,200}",
        r"все права защищены.{0,50}",
        r".{0,100}subscribe to our newsletter.{0,100}",
    )
)
_UI_LABEL_TRIM = "[]()*_|•·–—-:.»›→↑ \t"
_EDIT_LINK_RE = re.compile(
    r"^\[(?:edit|править|изменить|өңдеу)(?: source| code)?\]\([^)]*\)\]?$", re.I
)
_BRACKET_ONLY_RE = re.compile(r"^[\[\]]{1,2}$")
# "word.[2][3]": a citation marker glued to the preceding text (not "[1](https://...)").
_FOOTNOTE_RE = re.compile(r"(?<=\S)(?:\[\d{1,3}\](?!\())+")


def clean_content(content: ExtractedContent, max_chars: int) -> ExtractedContent:
    """Return a cleaned copy of `content`; the text is at most `max_chars` long.

    `links` is narrowed to URLs that still occur in the cleaned text, so the rule "the summary
    may only offer links found in the source" survives clean-up and truncation. When the text
    is cut, `meta` gains `truncated="true"` and `original_chars`.
    """
    is_html = content.source_format is SourceFormat.HTML

    lines = _normalize_characters(content.text).split("\n")
    if is_html:
        lines = _join_indented_continuations(lines)
    lines = [" ".join(line.split()) for line in lines]
    if is_html:
        lines = [_FOOTNOTE_RE.sub("", line) for line in lines]
        lines = [line for line in lines if not _is_web_noise(line)]
    lines = _drop_duplicate_lines(lines, distant_too=is_html)

    text = _collapse_blank_lines(lines)
    meta = dict(content.meta)
    if len(text) > max_chars:
        meta.update(truncated="true", original_chars=str(len(text)))
        text = _truncate(text, max_chars)

    links = tuple(link for link in content.links if _url_occurs(link, text))
    return replace(content, text=text, links=links, meta=meta)


def ensure_enough_text(content: ExtractedContent, min_chars: int = MIN_TEXT_CHARS) -> None:
    """Raise `TooLittleTextError` instead of letting the LLM guess from a near-empty source."""
    if len(content.text) < min_chars:
        raise TooLittleTextError(content.source_format, len(content.text), min_chars)


def _url_occurs(url: str, text: str) -> bool:
    """True if `url` appears as a whole URL, not merely as the start of a longer one
    (`https://example.edu` is not present in `https://example.edu/apply`)."""
    return re.search(rf"{re.escape(url)}{_URL_END}", text) is not None


def _normalize_characters(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for raw, replacement in _LINE_BREAKS.items():
        text = text.replace(raw, replacement)
    text = text.translate(_CHAR_TABLE)
    # Remaining control characters (NUL, escape codes...) are never content; \n and \t stay.
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")


def _is_structural(line: str) -> bool:
    return bool(_STRUCTURAL_RE.match(line))


def _join_indented_continuations(lines: list[str]) -> list[str]:
    """HTML pages keep their source line breaks and indentation inside paragraphs; a line that
    starts indented right after a prose line is the same paragraph. Headings, list items and
    table rows are never joined, and unindented lines are never guessed to be continuations."""
    joined: list[str] = []
    for line in lines:
        stripped = line.strip()
        previous = joined[-1].strip() if joined else ""
        if (
            line[:1].isspace()
            and stripped
            and previous
            and not _is_structural(previous)
            and not _is_structural(stripped)
        ):
            joined[-1] = f"{joined[-1].rstrip()} {stripped}"
        else:
            joined.append(line)
    return joined


def _is_web_noise(line: str) -> bool:
    if not line:
        return False
    if _BRACKET_ONLY_RE.match(line) or _EDIT_LINK_RE.match(line):
        return True
    if _UI_LABEL_RE.match(line.strip(_UI_LABEL_TRIM)):  # a line holding a URL can't match
        return True
    plain = _MARKDOWN_LINK_RE.sub(r"\1", line)
    return any(pattern.fullmatch(plain) for pattern in _PHRASE_NOISE_RES)


def _drop_duplicate_lines(lines: list[str], *, distant_too: bool) -> list[str]:
    kept: list[str] = []
    previous_key = ""
    seen_long: set[str] = set()
    for line in lines:
        if not line:
            kept.append(line)
            continue
        if _TABLE_ROW_RE.match(line):  # identical rows can be legitimate
            kept.append(line)
            previous_key = ""
            continue
        key = line.casefold()
        if len(key) >= _MIN_ADJACENT_DUPLICATE_CHARS and key == previous_key:
            continue  # repeated right after itself (blank lines between do not count)
        if distant_too and len(key) >= _MIN_DISTANT_DUPLICATE_CHARS:
            if key in seen_long:
                continue
            seen_long.add(key)
        kept.append(line)
        previous_key = key
    return kept


def _collapse_blank_lines(lines: list[str]) -> str:
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def _truncate(text: str, max_chars: int) -> str:
    budget = max_chars - len(TRUNCATION_MARKER) - 2
    if budget < 1:
        return text[:max_chars].rstrip()
    cut = text[:budget]
    last_break = cut.rfind("\n")
    if last_break >= budget * _TRUNCATE_AT_LINE_IF_KEEPS:
        cut = cut[:last_break]
    return f"{cut.rstrip()}\n\n{TRUNCATION_MARKER}"
