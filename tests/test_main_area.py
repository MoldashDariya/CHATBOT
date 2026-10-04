from pathlib import Path

import pytest
import trafilatura
from lxml import html as lxml_html
from trafilatura.utils import load_html

from bot.extractors.base import RawSource
from bot.extractors.html import HtmlExtractor
from bot.extractors.main_area import find_main_area, main_area_text

FIXTURES = Path(__file__).parent / "fixtures"
HUB_URL = "https://www.example.edu/apply"


def text_of(body: str) -> str:
    return main_area_text(lxml_html.fromstring(f"<html><body>{body}</body></html>"))


# --- the serializer -----------------------------------------------------------------------


def test_blocks_become_lines_in_document_order() -> None:
    body = (
        "<main><h1>Title</h1><p>First paragraph.</p><div>A div</div>"
        "<h2>Next</h2><p>Last.</p></main>"
    )
    assert text_of(body) == "Title\nFirst paragraph.\nA div\nNext\nLast."


def test_inline_markup_is_flattened_and_whitespace_collapsed() -> None:
    body = (
        "<main><p>Some <strong>bold</strong>,\n   <em>italic</em> and <span>plain</span> "
        "text.</p></main>"
    )
    assert text_of(body) == "Some bold, italic and plain text."


def test_list_items_get_bullets_and_nested_content_stays_under_its_item() -> None:
    body = "<main><ul><li>One<ul><li>Nested</li></ul></li><li>Two</li></ul></main>"
    assert text_of(body) == "- One\n  - Nested\n- Two"


def test_links_are_written_the_way_trafilatura_writes_them() -> None:
    body = (
        '<main><p>See <a href="https://x.org/a?b=1">the page</a> and '
        '<a href="/rel">relative</a>.</p></main>'
    )
    # Only absolute http(s) targets are kept; the HTML extractor makes them absolute first.
    assert text_of(body) == "See [the page](https://x.org/a?b=1) and relative."


def test_a_list_item_that_is_only_a_link_keeps_its_text_and_target() -> None:
    body = (
        '<main><ul><li><a href="https://x.org/m">Medicine: apply by 15 October 2026</a></li>'
        "</ul></main>"
    )
    assert text_of(body) == "- [Medicine: apply by 15 October 2026](https://x.org/m)"


def test_tables_become_pipe_rows_and_empty_rows_vanish() -> None:
    body = (
        "<main><table><tr><th>Level</th><th>Tuition</th></tr>"
        "<tr><td>Undergraduate</td><td>9,000 EUR</td></tr>"
        "<tr><td></td><td> </td></tr></table></main>"
    )
    assert text_of(body) == "| Level | Tuition |\n| Undergraduate | 9,000 EUR |"


def test_line_breaks_split_lines() -> None:
    assert text_of("<main><p>one<br>two<br/>three</p></main>") == "one\ntwo\nthree"


@pytest.mark.parametrize(
    "noise",
    [
        "<nav>Menu links</nav>",
        "<header>Site header</header>",
        "<footer>Site footer</footer>",
        "<aside>Related</aside>",
        "<form><input value='x'>Subscribe</form>",
        "<script>alert(1)</script>",
        "<style>.a{color:red}</style>",
        "<noscript>Enable JavaScript</noscript>",
        "<button>Click me</button>",
        '<div role="navigation">Nav by role</div>',
        '<div role="banner">Banner by role</div>',
        '<div role="complementary">Aside by role</div>',
        "<div hidden>Hidden attribute</div>",
        '<div aria-hidden="true">Hidden for readers</div>',
        '<p style="display:none">Hidden by style</p>',
        '<p style="visibility: hidden">Hidden by style</p>',
        '<div class="cookie-banner">We use cookies</div>',
        '<div id="cookie_consent">Consent</div>',
        '<div class="social-share">Share</div>',
        '<div class="breadcrumbs">Home / Study</div>',
        '<div class="newsletter popup">Subscribe</div>',
        "<!-- a comment -->",
    ],
)
def test_noise_inside_the_main_area_is_left_out(noise: str) -> None:
    result = text_of(f"<main><p>Real content here.</p>{noise}<p>More content.</p></main>")

    assert result == "Real content here.\nMore content."


@pytest.mark.parametrize(
    "name", ["shared-content", "adobe-products", "broadcast", "socialist-history"]
)
def test_class_names_that_merely_contain_a_noise_word_are_not_noise(name: str) -> None:
    assert "Kept" in text_of(f'<main><div class="{name}">Kept</div></main>')


def test_text_after_a_skipped_element_is_kept() -> None:
    assert text_of("<main><p>Before<script>x()</script> after.</p></main>") == "Before after."


def test_the_root_is_chosen_from_the_pages_own_landmarks() -> None:
    page = lxml_html.fromstring(
        "<html><body><div>Outside</div><main><p>Inside main</p></main>"
        "<article><p>Article</p></article></body></html>"
    )
    assert find_main_area(page).tag == "main"  # type: ignore[union-attr]

    role = lxml_html.fromstring('<html><body><div role="main"><p>By role</p></div></body></html>')
    assert find_main_area(role).get("role") == "main"  # type: ignore[union-attr]

    article = lxml_html.fromstring(
        "<html><body><div>x</div><article><p>Only</p></article></body></html>"
    )
    assert find_main_area(article).tag == "article"  # type: ignore[union-attr]


def test_a_page_without_landmarks_yields_nothing() -> None:
    assert text_of("<div><p>No main area declared.</p></div>") == ""


def test_an_extremely_nested_page_does_not_crash() -> None:
    deep = "<div>" * 3000 + "text" + "</div>" * 3000
    page = lxml_html.fromstring(f"<html><body><main>{deep}</main></body></html>")

    assert main_area_text(page) in ("", "text")  # either is fine; an exception is not


# --- inside the HTML extractor ------------------------------------------------------------


def hub_source() -> RawSource:
    return RawSource(
        url=HUB_URL, body=(FIXTURES / "link_hub.html").read_bytes(), content_type="text/html"
    )


def test_premise_trafilatura_alone_drops_the_link_only_lists() -> None:
    """If this ever fails, trafilatura improved and the fallback may no longer be needed."""
    tree = load_html((FIXTURES / "link_hub.html").read_bytes())
    plain = trafilatura.extract(tree, url=HUB_URL, include_links=True, include_tables=True) or ""

    assert "Applying for 2027 entry" in plain
    assert "15 October 2026" not in plain  # the deadlines are gone


async def test_the_extractor_recovers_deadline_lists_made_of_links() -> None:
    result = await HtmlExtractor().extract(hub_source())

    assert result.meta["text_source"] == "main_area"
    for deadline in [
        "Medicine: apply by 15 October 2026",
        "Law: apply by 15 January 2027",
        "Engineering: apply by 29 January 2027",
        "Music: apply by 1 October 2026 (audition required)",
        "Late applications: until 30 June 2027",
    ]:
        assert deadline in result.text
    assert "Step 3: pay the application fee of 75 EUR" in result.text
    assert "| Undergraduate | 9,000 EUR per year |" in result.text


async def test_recovered_text_keeps_headings_before_their_lists_and_the_page_order() -> None:
    text = (await HtmlExtractor().extract(hub_source())).text

    order = [
        "Applying for 2027 entry",
        "Application deadlines",
        "Medicine",
        "How to apply",
        "Step 1",
        "Fees",
    ]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)


async def test_recovered_text_excludes_everything_around_the_main_area_and_the_noise_in_it() -> (
    None
):
    result = await HtmlExtractor().extract(hub_source())

    noises = [
        "Accept all cookies",
        "Home",
        "Related",
        "New campus",
        "Copyright 2026",
        "Share on Facebook",
        "Hidden text",
        "a comment",
    ]
    for noise in noises:
        assert noise not in result.text


async def test_recovered_links_are_absolute_and_reported() -> None:
    result = await HtmlExtractor().extract(hub_source())

    assert "https://www.example.edu/apply/medicine" in result.links
    assert "https://www.example.edu/apply/account" in result.links
    assert all(link.startswith("https://") for link in result.links)
    for link in result.links:
        assert link in result.text


async def test_an_ordinary_article_still_uses_trafilatura() -> None:
    body = (FIXTURES / "university_admissions.html").read_bytes()

    result = await HtmlExtractor().extract(RawSource(url="https://www.example.edu/a", body=body))

    assert "text_source" not in result.meta
    assert "Accept all cookies" not in result.text


async def test_a_page_without_a_main_area_is_unaffected() -> None:
    body = b"<html><body><div><p>" + b"Some admissions text. " * 40 + b"</p></div></body></html>"

    result = await HtmlExtractor().extract(RawSource(url="https://x.edu/", body=body))

    assert "text_source" not in result.meta
    assert "Some admissions text." in result.text


async def test_the_javascript_shell_stays_empty() -> None:
    body = (FIXTURES / "js_shell.html").read_bytes()

    result = await HtmlExtractor().extract(RawSource(url="https://portal.example/", body=body))

    assert "text_source" not in result.meta
    assert len(result.text) < 100
