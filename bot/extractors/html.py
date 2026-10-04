import asyncio
import copy
import html
import logging
import re
from urllib.parse import urldefrag, urljoin, urlsplit

import trafilatura
from lxml.html import HtmlElement
from trafilatura.utils import load_html

from bot.errors import ExtractionError
from bot.extractors.base import ExtractedContent, RawSource, clean_meta, find_urls
from bot.extractors.main_area import main_area_text
from bot.pipeline.detector import SourceFormat

logger = logging.getLogger(__name__)

# Below this, a second pass with a more permissive extraction is tried before giving up.
_THIN_TEXT_CHARS = 500

# The page's own main area replaces trafilatura's text when it is this much fuller: trafilatura
# discards blocks that are mostly links, which on a funding guide or admissions hub is content.
_MAIN_AREA_FACTOR = 1.5
_MAIN_AREA_MIN_CHARS = 200

# An ampersand that does not start a complete entity ("&name;" / "&#123;"). libxml2 decodes
# legacy entities even without the semicolon, so "?a=1&section=2&region=EU" would come out as
# "?a=1§ion=2®ion=EU". Browsers do not do that.
_BARE_AMPERSAND_RE = re.compile(rb"&(?![A-Za-z0-9#]{1,32};)")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

# Elements whose contents are left exactly as they are when separating glued neighbours.
_VERBATIM_TAGS = frozenset({"pre", "code", "script", "style", "textarea"})
# Superscripts and subscripts belong to what precedes them: "15<sup>th</sup>", "x<sup>2</sup>",
# "H<sub>2</sub>O", footnote marks. They never get a space.
_ATTACHED_TAGS = frozenset({"sup", "sub"})
_ORDINAL_SUFFIXES = frozenset({"st", "nd", "rd", "th"})
_MAX_SIBLING_CHILDREN = 8  # glue happens between small inline widgets, not page-sized blocks
_SEPARATION_BUDGET_CHARS = 5_000_000  # a hostile deep page must not make this quadratic

_WEB_SCHEMES = ("http", "https")
_NON_WEB_PREFIXES = ("javascript:", "mailto:", "tel:", "data:", "sms:")


class HtmlExtractor:
    """Main-content extraction with trafilatura: menus, cookie banners, ads and footers are
    dropped; headings, lists, tables (as `| a | b |` rows) and in-content links are kept.

    Links are fixed up around trafilatura, which on its own resolves relative links against
    the site root instead of the page, ignores `<base>`, downgrades `//host/x` to http and
    mangles query strings through `html.unescape`. So the DOM is prepared first (links made
    absolute, same-page anchors unwrapped) and mangled URLs are repaired afterwards.
    """

    async def extract(self, source: RawSource) -> ExtractedContent:
        return await asyncio.to_thread(self._extract, source)

    def _extract(self, source: RawSource) -> ExtractedContent:
        body = escape_bare_ampersands(source.body)
        try:
            tree = load_html(body)
            known_urls: set[str] = set()
            if tree is not None:
                known_urls = prepare_links(tree, source.url)
                separate_glued_elements(tree)
            document = tree if tree is not None else body

            text = restore_urls(self._main_text(document, source.url), known_urls)
            text, used_main_area = _prefer_fuller_main_area(text, tree)
            metadata = trafilatura.extract_metadata(_fresh(document), default_url=source.url)
        except Exception as exc:
            logger.warning("HTML extraction failed: %s", type(exc).__name__, exc_info=True)
            raise ExtractionError("corrupt") from exc

        canonical = metadata.url if metadata and metadata.url != source.url else None
        return ExtractedContent(
            text=text,
            title=(metadata.title.strip() if metadata and metadata.title else None) or None,
            source_url=source.url,
            source_format=SourceFormat.HTML,
            # Taken from the cleaned text, so navigation and footer links never get in.
            links=find_urls(text),
            meta=clean_meta(
                text_source="main_area" if used_main_area else None,
                # Trafilatura returns the page's declared canonical URL, else the one we gave it.
                canonical_url=canonical,
                published=metadata.date if metadata else None,
                site_name=metadata.sitename if metadata else None,
                author=metadata.author if metadata else None,
                description=metadata.description if metadata else None,
            ),
        )

    @staticmethod
    def _main_text(document: HtmlElement | bytes, url: str) -> str:
        def run(*, favor_recall: bool) -> str:
            return (
                trafilatura.extract(
                    _fresh(document),  # trafilatura edits the tree it is given
                    url=url,
                    include_links=True,
                    include_tables=True,
                    include_comments=False,
                    output_format="txt",
                    favor_recall=favor_recall,
                )
                or ""
            ).strip()

        text = run(favor_recall=False)
        if len(text) < _THIN_TEXT_CHARS:
            # Pages with unusual layouts: accept more candidate blocks, keep the longer result.
            text = max(text, run(favor_recall=True), key=len)
        return text


def _prefer_fuller_main_area(text: str, tree: HtmlElement | None) -> tuple[str, bool]:
    """(text, whether the main-area text was used). Never fails: this is a fallback."""
    if tree is None:
        return text, False
    try:
        alternative = main_area_text(tree)
    except Exception:
        logger.warning("Main-area extraction failed", exc_info=True)
        return text, False
    if len(alternative) >= _MAIN_AREA_MIN_CHARS and len(alternative) > _MAIN_AREA_FACTOR * len(
        text
    ):
        logger.info("Using main-area text: %d chars instead of %d", len(alternative), len(text))
        return alternative, True
    return text, False


def _fresh(document: HtmlElement | bytes) -> HtmlElement | bytes:
    return copy.deepcopy(document) if isinstance(document, HtmlElement) else document


def escape_bare_ampersands(body: bytes) -> bytes:
    """Protect URLs and text from libxml2's lenient entity decoding (see _BARE_AMPERSAND_RE).

    Works on bytes so the page's encoding is irrelevant for every ASCII-compatible one; the
    rare UTF-16 page is left alone rather than corrupted.
    """
    if body.startswith(_UTF16_BOMS):
        return body
    return _BARE_AMPERSAND_RE.sub(b"&amp;", body)


def prepare_links(tree: HtmlElement, page_url: str) -> set[str]:
    """Make every web link absolute (honouring `<base href>`) and unwrap same-page anchors.

    Returns the absolute URLs present on the page (link targets and URLs typed into the
    text), which `restore_urls` uses to repair them after extraction.
    """
    base = _document_base(tree, page_url)
    page = urldefrag(page_url)[0]
    urls: set[str] = set(find_urls(tree.text_content()))

    for anchor in list(tree.iter("a")):  # the loop edits the tree, so iterate over a copy
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_NON_WEB_PREFIXES):
            continue
        absolute = urljoin(base, href)
        if urlsplit(absolute).scheme not in _WEB_SCHEMES:
            continue
        if href.startswith("#") or (urldefrag(absolute)[0] == page and "#" in absolute):
            # A jump within this page is not a link worth keeping. Dropping the tag (not just
            # the href) keeps trafilatura from wrapping the label in stray brackets.
            anchor.drop_tag()
            continue
        anchor.set("href", absolute)
        urls.add(absolute)
    return urls


def _is_attached(element: HtmlElement) -> bool:
    """A superscript or subscript, or a wrapper holding nothing else: `<b><sup>st</sup></b>`."""
    if element.tag in _ATTACHED_TAGS:
        return True
    only_child = len(element) == 1 and not (element.text or "").strip()
    return only_child and isinstance(element[0].tag, str) and _is_attached(element[0])


def separate_glued_elements(tree: HtmlElement) -> int:
    """Put a space between neighbouring elements whose texts would otherwise run together.

    Pages put badges and labels next to values with no whitespace in the markup and let CSS
    space them: `<strong>15 July</strong><span>in 9 months</span>`. A browser shows two
    things, a text extractor reads "15 Julyin 9 months". Only element-to-element boundaries
    and text-to-`span` boundaries are touched, never `Sys<b>tem</b>` (styling inside a word)
    and never `<pre>`/`<code>`. Returns how many gaps were opened.
    """
    opened = 0
    budget = _SEPARATION_BUDGET_CHARS
    verbatim = {inner for tag in _VERBATIM_TAGS for node in tree.iter(tag) for inner in node.iter()}
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        following = element.getnext()
        if (
            following is None
            or not isinstance(following.tag, str)
            or following in verbatim
            or _is_attached(element)
            or _is_attached(following)
            or element.tail
            or len(element) > _MAX_SIBLING_CHILDREN
            or len(following) > _MAX_SIBLING_CHILDREN
        ):
            continue
        left, right = "".join(element.itertext()), "".join(following.itertext())
        budget -= len(left) + len(right)
        if budget < 0:
            break
        if (
            left
            and right
            and left[-1].isalnum()
            and right[0].isalnum()
            and not (left[-1].isdigit() and right.strip().lower() in _ORDINAL_SUFFIXES)
        ):
            element.tail = " "
            opened += 1

    # Text immediately followed by a <span> widget: "15 July<span>in 9 months</span>".
    for span in tree.iter("span"):
        previous = span.getprevious()
        holder = span.getparent()
        if holder is None or span in verbatim or len(span) > _MAX_SIBLING_CHILDREN:
            continue
        preceding = previous.tail if previous is not None else holder.text
        text = "".join(span.itertext())
        if preceding and text and preceding[-1].isalnum() and text[0].isalnum():
            if previous is not None:
                previous.tail = f"{preceding} "
            else:
                holder.text = f"{preceding} "
            opened += 1
    return opened


def _document_base(tree: HtmlElement, page_url: str) -> str:
    for base in tree.iter("base"):
        declared = (base.get("href") or "").strip()
        resolved = urljoin(page_url, declared) if declared else ""
        if urlsplit(resolved).scheme in _WEB_SCHEMES:
            return resolved
        break
    return page_url


def restore_urls(text: str, known_urls: set[str]) -> str:
    """Undo `html.unescape` damage (trafilatura applies it to its output, URLs included):
    "...&section=2" came out as "...§ion=2", "&copy=1" as "©=1"."""
    damaged = {html.unescape(url): url for url in known_urls if html.unescape(url) != url}
    for mangled in sorted(damaged, key=len, reverse=True):
        text = text.replace(mangled, damaged[mangled])
    return text
