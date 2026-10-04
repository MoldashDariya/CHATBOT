"""Text of a page's main area, built straight from the DOM.

trafilatura drops blocks that are mostly links, because on most sites those are navigation. On a
funding guide or an admissions hub they are the content: "Step 1: check ...", "Medicine: apply by
15 October" are all links. When the page declares a main area (`<main>`, `role=main`, `<article>`)
this module reads it directly, keeping order, headings, list items, tables and links, and the HTML
extractor uses the result when it is clearly fuller than trafilatura's.

Only the page's own landmarks are trusted: navigation, header, footer, aside, forms, scripts,
hidden elements and obvious cookie/share/ad widgets are skipped.
"""

import re
from collections.abc import Iterator

from lxml.html import HtmlElement

_NOISE_TAGS = frozenset(
    {
        "nav", "header", "footer", "aside", "form", "script", "style", "noscript", "template",
        "svg", "button", "select", "iframe", "dialog", "canvas", "audio", "video", "object",
    }
)  # fmt: skip
_NOISE_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search", "dialog"}
)
# Whole words in class/id ("cookie-banner", "social_share"), not substrings ("shared", "adobe").
_NOISE_NAME_RE = re.compile(
    r"(?<![a-z0-9])(?:cookies?|consent|gdpr|adverts?|ads?|share|social|breadcrumbs?|skip-?links?|"
    r"newsletter|popup|modal)(?![a-z0-9])",
    re.IGNORECASE,
)
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset(
    {
        "p", "div", "section", "article", "main", "ul", "ol", "dl", "dt", "dd", "blockquote",
        "pre", "figure", "figcaption", "address", "details", "summary", "fieldset", "hgroup",
    }
)  # fmt: skip


def find_main_area(tree: HtmlElement) -> HtmlElement | None:
    for xpath in ("//main", "//*[@role='main']", "//article"):
        found = tree.xpath(xpath)
        if found:
            return found[0]
    return None


def main_area_text(tree: HtmlElement) -> str:
    """Text of the page's main area, one block per line; "" if the page declares none or the
    document is too deeply nested to walk safely."""
    root = find_main_area(tree)
    if root is None:
        return ""
    lines: list[str] = []
    try:
        _walk(root, lines)
    except RecursionError:
        return ""
    return "\n".join(lines)


def _is_noise(element: HtmlElement) -> bool:
    tag = element.tag
    if not isinstance(tag, str):  # comments and processing instructions
        return True
    if tag in _NOISE_TAGS:
        return True
    if element.get("role") in _NOISE_ROLES or element.get("hidden") is not None:
        return True
    if element.get("aria-hidden") == "true":
        return True
    if _HIDDEN_STYLE_RE.search(element.get("style") or ""):
        return True
    return bool(_NOISE_NAME_RE.search(f"{element.get('class') or ''} {element.get('id') or ''}"))


def _walk(element: HtmlElement, out: list[str]) -> None:
    """Append the lines of `element`'s content to `out`."""
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join("".join(buffer).split())
        buffer.clear()
        if text:
            out.append(text)

    if element.text:
        buffer.append(element.text)
    for child in element:
        if _is_noise(child):
            if child.tail:
                buffer.append(child.tail)  # text after a skipped element still belongs here
            continue
        tag = child.tag
        if tag == "br":
            flush()
        elif tag in _HEADINGS:
            flush()
            heading = _inline_text(child)
            if heading:
                out.append(heading)
        elif tag == "table":
            flush()
            _table(child, out)
        elif tag == "li":
            flush()
            inner: list[str] = []
            _walk(child, inner)
            out.extend(_as_list_item(inner))
        elif tag in _BLOCKS:
            flush()
            _walk(child, out)
        else:
            buffer.append(_inline_text(child))
        if child.tail:
            buffer.append(child.tail)
    flush()


def _as_list_item(lines: list[str]) -> Iterator[str]:
    for index, line in enumerate(lines):
        yield f"- {line}" if index == 0 else f"  {line}"  # nested content stays under its item


def _table(table: HtmlElement, out: list[str]) -> None:
    for row in table.iter("tr"):
        cells = [
            " ".join(_inline_text(cell).split())
            for cell in row
            if isinstance(cell.tag, str) and cell.tag in {"td", "th"} and not _is_noise(cell)
        ]
        if any(cells):
            out.append(f"| {' | '.join(cells)} |")


def _inline_text(element: HtmlElement) -> str:
    """Text of an element and everything inside it flattened to one line; links become
    `[text](url)` the way trafilatura writes them. The element's tail is not included."""
    label = _flatten(element)
    if isinstance(element.tag, str) and element.tag == "a":
        href = (element.get("href") or "").strip()
        if label.strip() and href.startswith(("http://", "https://")):
            label = f"[{' '.join(label.split())}]({href})"
    return " ".join(label.split())


def _flatten(element: HtmlElement) -> str:
    pieces: list[str] = []
    if element.text:
        pieces.append(element.text)
    for child in element:
        if not isinstance(child.tag, str) or _is_noise(child):
            if child.tail:
                pieces.append(child.tail)
            continue
        if child.tag == "br":
            pieces.append(" ")
        elif child.tag == "a":
            pieces.append(_inline_text(child))
        else:
            pieces.append(_flatten(child))
        if child.tail:
            pieces.append(child.tail)
    return "".join(pieces)
