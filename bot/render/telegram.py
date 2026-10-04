"""`Summary` -> Telegram messages in HTML parse mode.

Telegram has no tables, so the "table" of the product idea is a card of emoji-headed sections.

Safety rules this module keeps:
- Every dynamic string is escaped and clipped before it gets a tag, and a tag never stays open
  across a line break. So the text can be split between lines at any point without breaking
  markup, and no field can grow a line anywhere near the message limit.
- Only `<b>`, `<i>`, `<a href>` and `<code>` are produced (the subset Telegram accepts), and an
  `href` is only ever an http(s) URL.
- The source line is always the last line of the last message.
"""

import html
import re
from urllib.parse import urlsplit

from bot.pipeline.schema import Confidence, Deadline, SourceClass, Summary
from bot.render.texts import text

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 4000  # headroom under the real limit

# Clip lengths (characters of visible text).
MAX_TITLE = 200
MAX_OVERVIEW = 1200
MAX_ITEM = 400
MAX_LABEL = 120
MAX_QUOTE = 140
MAX_WARNING = 700
MAX_URL_TEXT = 200

_TAG_RE = re.compile(r"<[^>]+>")
_BULLET = "•"
_CONFIDENCE_EMOJI = {Confidence.HIGH: "🟢", Confidence.MEDIUM: "🟡", Confidence.LOW: "🔴"}


# --- text helpers -------------------------------------------------------------------------


def _esc(value: str) -> str:
    """Escape exactly the four characters Telegram's HTML dialect defines named entities for.
    (`html.escape` would also turn an apostrophe into the hex entity `&#x27;`, which Telegram
    does not promise to understand.)"""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _clip(value: str, limit: int) -> str:
    """One line, at most `limit` characters, cut at a word where that costs little."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    cut = value[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def _snippet(value: str, anchor: str, limit: int) -> str:
    """At most `limit` characters of `value` that contain `anchor` (the date a quote supports),
    cut at words, with "…" marking what was left out. A quote about a late deadline must not be
    shown cut off just before the late date. Without the anchor this is a plain head clip."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    anchor = " ".join(anchor.split())
    index = value.lower().find(anchor.lower()) if anchor else -1
    if index == -1 or len(anchor) >= limit - 2:
        return _clip(value, limit)

    room = limit - 2  # two characters for the ellipses
    start = max(0, min(index - room // 4, len(value) - room))
    end = start + room
    if start > 0:  # begin at a word
        space = value.find(" ", start)
        if 0 <= space < index:
            start = space + 1
    if end < len(value):  # end at a word
        space = value.rfind(" ", index + len(anchor), end)
        if space > index:
            end = space
    cut = value[start:end].strip()
    return f"{'…' if start > 0 else ''}{cut}{'…' if end < len(value) else ''}"


def _is_web_url(url: str) -> bool:
    return urlsplit(url.strip()).scheme in ("http", "https")


def _domain(url: str) -> str:
    """The host as shown next to a link, without a leading "www." (it tells the reader nothing)."""
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    try:
        return host.encode("ascii").decode("idna")  # show Cyrillic domains as people know them
    except UnicodeError:
        return host


def visible_length(markup: str) -> int:
    """Length as Telegram counts it: tags and href values do not count, entities count as one
    character, and astral characters (most emoji) count as two UTF-16 units."""
    plain = html.unescape(_TAG_RE.sub("", markup))
    return len(plain.encode("utf-16-le")) // 2


# --- the card -----------------------------------------------------------------------------


def render_summary(summary: Summary) -> list[str]:
    """The card as one or more messages, each within Telegram's limit."""
    lang = summary.language

    blocks: list[list[str]] = [_header(summary, lang)]
    if summary.overview:
        blocks.append([_esc(_clip(summary.overview, MAX_OVERVIEW))])
    blocks.append(_section("⏰", "deadlines", lang, [_deadline(d) for d in summary.deadlines]))
    blocks.append(_section("✅", "key_points", lang, [_plain(i) for i in summary.key_points]))
    blocks.append(_section("👤", "eligibility", lang, [_plain(i) for i in summary.eligibility]))
    blocks.append(
        _section(
            "💰",
            "costs",
            lang,
            [
                _item(f"{_esc(_clip(c.item, MAX_LABEL))}: {_esc(_clip(c.amount_text, MAX_ITEM))}")
                for c in summary.costs
            ],
        )
    )
    blocks.append(_section("📋", "requirements", lang, [_plain(i) for i in summary.requirements]))
    blocks.append(_links_section(summary, lang))

    notes = _notes(summary, lang)
    if notes:
        blocks.append([f"⚠️ <b>{_esc(text(lang, 'notes'))}</b>", *notes])
    blocks.append([_confidence_line(summary, lang)])
    if summary.cached_minutes_ago is not None:
        age = _age(lang, summary.cached_minutes_ago)
        blocks.append([f"<i>{_esc(text(lang, 'cached_note', age=age))}</i>"])
    if summary.extra_sources:
        blocks.append(_extra_sources(summary, lang))
    blocks.append([_source_line(summary, lang)])  # always last
    return _pack(blocks)


def _header(summary: Summary, lang: str) -> list[str]:
    title = _clip(summary.title or "", MAX_TITLE) or text(lang, "untitled")
    kind = text(lang, f"type_{summary.source_type.value}")
    if summary.organization:
        meta = f"🏛 {_esc(_clip(summary.organization, MAX_LABEL))} · {_esc(kind)}"
    else:
        meta = f"🗂 {_esc(kind)}"
    return [f"📌 <b>{_esc(title)}</b>", meta]


def _item(inner_markup: str) -> str:
    return f"{_BULLET} {inner_markup}"


def _plain(value: str) -> str:
    return _item(_esc(_clip(value, MAX_ITEM)))


def _section(emoji: str, key: str, lang: str, items: list[str]) -> list[str]:
    heading = f"{emoji} <b>{_esc(text(lang, key))}</b>"
    if not items:
        items = [f"<i>— {_esc(text(lang, 'not_stated'))}</i>"]
    return [heading, *items]


def _deadline(deadline: Deadline) -> str:
    marks = ""
    if not deadline.verified:
        marks += " ❓"
    if deadline.possibly_passed:
        marks += " ⏳"
    label = _esc(_clip(deadline.label, MAX_LABEL))
    when = _esc(_clip(deadline.date_text, MAX_LABEL))
    line = f"{_BULLET} {label}: <b>{when}</b>{marks}"
    quote = _snippet(
        deadline.quote.strip().strip("|").replace(" | ", " · "), deadline.date_text, MAX_QUOTE
    )
    return f"{line}\n  <i>«{_esc(quote)}»</i>" if quote else line


def _links_section(summary: Summary, lang: str) -> list[str]:
    items = []
    for link in summary.useful_links:
        if not _is_web_url(link.url):
            continue  # an href must be http(s); the filter upstream already guarantees it
        title = _esc(_clip(link.title, MAX_LABEL) or _domain(link.url))
        href = _esc(link.url.strip())
        # The domain goes in <code>: as plain text Telegram would auto-link it, and every line
        # would show two tappable links.
        items.append(
            f'{_BULLET} <a href="{href}">{title}</a> — <code>{_esc(_domain(link.url))}</code>'
        )
    heading = f"🔗 <b>{_esc(text(lang, 'links'))}</b>"
    return [heading, *(items or [f"<i>— {_esc(text(lang, 'no_links'))}</i>"])]


def _notes(summary: Summary, lang: str) -> list[str]:
    """Most important first: what is wrong with the dates, then coverage, then the rest."""
    notes: list[str] = []
    if summary.source_type is SourceClass.OTHER:
        notes.append(text(lang, "note_not_admission"))
    if any(not d.verified for d in summary.deadlines):
        notes.append(text(lang, "note_unverified"))
    if any(d.possibly_passed for d in summary.deadlines):
        notes.append(text(lang, "note_passed"))
    if summary.truncated:
        notes.append(text(lang, "note_truncated"))
    if (
        summary.pages_read is not None
        and summary.pages_total is not None
        and summary.pages_read < summary.pages_total
    ):
        notes.append(text(lang, "note_pages", read=summary.pages_read, total=summary.pages_total))
    if summary.dropped_links:
        notes.append(text(lang, "note_dropped_links", n=summary.dropped_links))
    notes.extend(_clip(warning, MAX_WARNING) for warning in summary.warnings)
    return [_item(_esc(note)) for note in notes]


def _confidence_line(summary: Summary, lang: str) -> str:
    emoji = _CONFIDENCE_EMOJI[summary.confidence]
    label = text(lang, f"confidence_{summary.confidence.value}")
    return f"{emoji} {_esc(text(lang, 'confidence'))}: {_esc(label)}"


def _age(lang: str, minutes: int) -> str:
    """How long ago, in the unit a person would use."""
    if minutes < 1:
        return text(lang, "cached_age_now")
    if minutes < 60:
        return text(lang, "cached_age_minutes", n=minutes)
    if minutes < 48 * 60:
        return text(lang, "cached_age_hours", n=minutes // 60)
    return text(lang, "cached_age_days", n=minutes // (24 * 60))


def _extra_sources(summary: Summary, lang: str) -> list[str]:
    """Pages read besides the one the user sent: the facts may come from them."""
    items = []
    for url in summary.extra_sources:
        if _is_web_url(url):
            items.append(
                f'{_BULLET} <a href="{_esc(url.strip())}">{_esc(_clip(url, MAX_URL_TEXT))}</a>'
            )
    return [f"📎 <b>{_esc(text(lang, 'also_read'))}:</b>", *items] if items else []


def _source_line(summary: Summary, lang: str) -> str:
    label = f"🔎 <b>{_esc(text(lang, 'source'))}:</b>"
    url = summary.source_url.strip()
    shown = _esc(_clip(url, MAX_URL_TEXT))
    if _is_web_url(url):
        return f'{label} <a href="{_esc(url)}">{shown}</a>'
    return f"{label} <code>{shown}</code>"


# --- splitting into messages --------------------------------------------------------------


def _pack(blocks: list[list[str]], limit: int = SAFE_LIMIT) -> list[str]:
    """Fill messages with whole blocks (separated by a blank line); a block that does not fit
    on its own is split between its lines."""
    messages: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            messages.append(current)
            current = ""

    for block in blocks:
        body = "\n".join(block)
        if visible_length(body) <= limit:
            candidate = f"{current}\n\n{body}" if current else body
            if visible_length(candidate) <= limit:
                current = candidate
                continue
            flush()
            current = body
            continue

        flush()
        for part in _split_block(block, limit):
            candidate = f"{current}\n\n{part}" if current else part
            if current and visible_length(candidate) > limit:
                flush()
                current = part
            else:
                current = candidate
    flush()
    return messages


def _split_block(block: list[str], limit: int) -> list[str]:
    """Split an oversize block between its lines. The caller starts it on a fresh message, so
    its heading always shares a message with the first item."""
    units = list(block)
    parts: list[str] = []
    current = ""
    for unit in units:
        if visible_length(unit) > limit:
            unit = _hard_cut(unit, limit)  # cannot happen with the clipping above
        candidate = f"{current}\n{unit}" if current else unit
        if current and visible_length(candidate) > limit:
            parts.append(current)
            current = unit
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _hard_cut(markup: str, limit: int) -> str:
    """Last resort for a single oversize unit: drop its formatting rather than risk a broken tag."""
    plain = html.unescape(_TAG_RE.sub("", markup))[: limit - 1]
    cut = _esc(plain) + "…"
    while visible_length(cut) > limit:  # emoji count twice, so shrink until it really fits
        plain = plain[: max(1, int(len(plain) * 0.9))]
        cut = _esc(plain) + "…"
    return cut
