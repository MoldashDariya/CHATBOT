from pathlib import Path

import pytest

from bot.errors import ExtractionError
from bot.extractors import html as html_module
from bot.extractors.base import RawSource
from bot.extractors.html import HtmlExtractor, escape_bare_ampersands
from bot.pipeline.detector import SourceFormat

FIXTURES = Path(__file__).parent / "fixtures"
UNIVERSITY_URL = "https://www.example.edu/admissions/undergraduate"


def load(name: str, url: str) -> RawSource:
    return RawSource(url=url, body=(FIXTURES / name).read_bytes(), content_type="text/html")


@pytest.fixture
async def university():  # type: ignore[no-untyped-def]
    return await HtmlExtractor().extract(load("university_admissions.html", UNIVERSITY_URL))


async def test_keeps_the_admissions_facts(university) -> None:  # type: ignore[no-untyped-def]
    text = university.text
    for fact in [
        "Undergraduate Admissions 2027",
        "1 November 2026",
        "15 January 2027",
        "1 December 2026",
        "IELTS 6.5",
        "Two letters of recommendation",
        "14,500 EUR",
        "75 EUR",
        "up to 50% of tuition",
    ]:
        assert fact in text


async def test_drops_navigation_ads_banners_and_footer(university) -> None:  # type: ignore[no-untyped-def]
    text = university.text
    for noise in [
        "Accept all cookies",
        "Student login",
        "Alumni network",
        "Win a free laptop",
        "Subscribe to our newsletter",
        "Read also",
        "New campus opens",
        "All rights reserved",
        "Privacy policy",
        "dataLayer",
    ]:
        assert noise not in text


async def test_table_rows_stay_together(university) -> None:  # type: ignore[no-untyped-def]
    # Deadlines usually live in tables: label and date must stay on one line.
    assert "| Early round | 1 November 2026 |" in university.text
    assert "| Scholarship applications | 1 December 2026 |" in university.text


async def test_links_are_absolute_http_and_only_from_the_main_content(university) -> None:  # type: ignore[no-untyped-def]
    assert university.links == (
        "https://www.example.edu/scholarships/merit",
        "https://www.example.edu/apply/online",
        "https://www.example.edu/admissions/faq?lang=en",
    )
    # Menu, footer and "read also" links are boilerplate; mailto: is not a web link.
    joined = " ".join(university.links)
    for boilerplate in ["/login", "/alumni", "/privacy", "/terms", "campus-opening", "mailto"]:
        assert boilerplate not in joined


async def test_every_link_really_occurs_in_the_text(university) -> None:  # type: ignore[no-untyped-def]
    for link in university.links:
        assert link in university.text


async def test_title_and_metadata(university) -> None:  # type: ignore[no-untyped-def]
    assert university.title == "Undergraduate Admissions 2027"
    assert university.source_format is SourceFormat.HTML
    assert university.source_url == UNIVERSITY_URL
    assert university.meta == {
        "published": "2026-09-01",
        "site_name": "Example University",
        "description": "How to apply to undergraduate programmes at Example University.",
    }


async def test_news_article_drops_comments_and_sharing_widgets() -> None:
    result = await HtmlExtractor().extract(
        load("news_article.html", "https://dailycampus.example/news/deadline")
    )

    assert "16 October 2026" in result.text
    assert "300 EUR" in result.text
    for noise in [
        "first!",
        "Comments (214)",
        "Share on Facebook",
        "Ten celebrity diets",
        "Advertise",
    ]:
        assert noise not in result.text
    assert result.links == (
        "https://scholarships.example.gov/partners",
        "https://scholarships.example.gov/apply",
    )
    assert result.meta["published"] == "2026-10-02"
    assert result.meta["author"] == "Jane Doe"


async def test_javascript_shell_yields_almost_no_text() -> None:
    # The extractor reports what is there; the "too little text" decision and the user
    # message about JS/login/paywall belong to the cleaner (task 1.17).
    result = await HtmlExtractor().extract(load("js_shell.html", "https://portal.example/"))

    assert len(result.text) < 100
    assert result.links == ()
    assert result.title == "Scholarship Portal"


async def test_legacy_encoding_is_decoded() -> None:
    page = (
        '<html><head><meta charset="windows-1251"><title>Приём документов</title></head><body>'
        "<article><h1>Приём документов в университет</h1>"
        "<p>Приём документов на бакалавриат продлится до 15 июля 2027 года. "
        "Абитуриентам необходимо "
        "предоставить аттестат, результаты ЕНТ не ниже 100 баллов и заявление установленной формы. "
        "Стоимость обучения составляет 1 200 000 тенге в год, предусмотрены гранты и скидки.</p>"
        "<p>Подробности на странице <a href='/grants'>грантов</a>.</p></article></body></html>"
    ).encode("cp1251")

    result = await HtmlExtractor().extract(
        RawSource(url="https://uni.example.kz/priem", body=page, content_type="text/html")
    )

    assert "до 15 июля 2027 года" in result.text
    assert "1 200 000 тенге" in result.text
    assert result.links == ("https://uni.example.kz/grants",)


async def test_garbage_bytes_give_empty_text_not_a_crash() -> None:
    result = await HtmlExtractor().extract(
        RawSource(url="https://example.edu/", body=bytes(range(256)) * 20)
    )
    assert result.text == "" or len(result.text) < 100
    assert result.links == ()


async def test_thin_result_triggers_a_second_permissive_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_extract(body: bytes, **kwargs: object) -> str:
        calls.append(bool(kwargs["favor_recall"]))
        return "x" * 900 if kwargs["favor_recall"] else "short"

    monkeypatch.setattr(html_module.trafilatura, "extract", fake_extract)

    result = await HtmlExtractor().extract(RawSource(url="https://example.edu/", body=b"<html/>"))

    assert calls == [False, True]
    assert result.text == "x" * 900


async def test_substantial_result_does_not_need_a_second_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_extract(body: bytes, **kwargs: object) -> str:
        calls.append(bool(kwargs["favor_recall"]))
        return "y" * 800

    monkeypatch.setattr(html_module.trafilatura, "extract", fake_extract)
    await HtmlExtractor().extract(RawSource(url="https://example.edu/", body=b"<html/>"))

    assert calls == [False]


async def test_parser_failure_becomes_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(body: bytes, **kwargs: object) -> str:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(html_module.trafilatura, "extract", boom)

    with pytest.raises(ExtractionError) as exc_info:
        await HtmlExtractor().extract(RawSource(url="https://example.edu/", body=b"<html/>"))
    assert exc_info.value.reason == "corrupt"


async def test_canonical_url_reported_only_when_it_differs_from_the_fetched_url() -> None:
    shared_link = RawSource(
        url="https://short.link/x1?utm_source=telegram",
        body=(FIXTURES / "university_admissions.html").read_bytes(),
    )
    result = await HtmlExtractor().extract(shared_link)
    assert result.meta["canonical_url"] == "https://www.example.edu/admissions/undergraduate"
    assert result.source_url == "https://short.link/x1?utm_source=telegram"  # what was fetched

    direct = await HtmlExtractor().extract(load("university_admissions.html", UNIVERSITY_URL))
    assert "canonical_url" not in direct.meta


PADDING = (
    "<p>The application window for the 2027 programme opens in the autumn and closes in the "
    "spring. Candidates must prepare all documents in advance and submit them through the "
    "official portal before the final deadline stated on this page, otherwise the application "
    "is not considered.</p>"
)


def article(inner: str, *, head: str = "", url: str = "https://example.edu/") -> RawSource:
    page = (
        f"<html><head><title>Apply</title>{head}</head><body><article><h1>How to apply</h1>"
        f"{PADDING}{inner}</article></body></html>"
    )
    return RawSource(url=url, body=page.encode())


# --- links --------------------------------------------------------------------------------


def in_prose(link_html: str) -> str:
    """trafilatura drops paragraphs that are mostly links (they look like navigation), so a
    link under test has to sit inside a real sentence."""
    return (
        "<p>For more detail about this step of the application process please read "
        f"{link_html} carefully before you continue with the online form.</p>"
    )


async def test_query_parameters_that_look_like_legacy_entities_survive() -> None:
    # libxml2/html.unescape would turn &section= into the section sign, &region= into the
    # registered sign, &copy= into the copyright sign, &not= into the negation sign...
    url = "https://portal.example.edu/apply?lang=en&section=2&region=EU&copy=1&not=2&para=3&times=4"
    inner = f'<p>Portal: <a href="{url}">apply here</a>.</p>'

    result = await HtmlExtractor().extract(article(inner))

    assert result.links == (url,)
    assert url in result.text


async def test_urls_typed_into_the_text_survive_too() -> None:
    url = "https://portal.example.edu/apply?section=2&region=EU"
    result = await HtmlExtractor().extract(article(f"<p>Address: {url} (copy it)</p>"))

    assert url in result.text
    assert result.links == (url,)


async def test_properly_terminated_entities_still_decode() -> None:
    inner = (
        "<p>Caf&eacute; &amp; bar, price &#8364;10, copyright &#169; 2026, AT&T and Q&A stay "
        'as written, link <a href="https://e.edu/?a=1&amp;b=2">here</a>.</p>'
    )
    result = await HtmlExtractor().extract(article(inner))

    assert "Café & bar, price €10, copyright © 2026, AT&T and Q&A stay as written" in result.text
    assert result.links == ("https://e.edu/?a=1&b=2",)


async def test_utf16_pages_are_not_touched_by_the_byte_level_fix() -> None:
    page = "<html><body>a&b</body></html>".encode("utf-16")
    assert escape_bare_ampersands(page) == page


async def test_relative_links_resolve_against_the_page_not_the_site_root() -> None:
    hrefs = ["apply.html", "../fees", "/abs/root", "?tab=2", "//cdn.example.org/forms.pdf"]
    inner = "".join(in_prose(f'<a href="{href}">link {n}</a>') for n, href in enumerate(hrefs))
    source = article(inner, url="https://example.edu/admissions/undergraduate/index")

    result = await HtmlExtractor().extract(source)

    assert result.links == (
        "https://example.edu/admissions/undergraduate/apply.html",
        "https://example.edu/admissions/fees",
        "https://example.edu/abs/root",
        "https://example.edu/admissions/undergraduate/index?tab=2",
        "https://cdn.example.org/forms.pdf",  # inherits https from the page
    )


async def test_base_tag_is_honoured() -> None:
    source = article(
        in_prose('<a href="rules.html">the rules</a>'),
        head='<base href="https://docs.example.edu/handbook/">',
        url="https://example.edu/admissions/",
    )
    assert (await HtmlExtractor().extract(source)).links == (
        "https://docs.example.edu/handbook/rules.html",
    )


async def test_same_page_anchors_are_plain_text_not_links() -> None:
    inner = (
        in_prose('<a href="#details">Jump to details</a>')
        + in_prose('<a href="/admissions/page#fees">fees on this page</a>')
        + in_prose('<a href="/other#fees">fees elsewhere</a>')
    )
    result = await HtmlExtractor().extract(
        article(inner, url="https://example.edu/admissions/page")
    )

    assert result.links == ("https://example.edu/other#fees",)
    assert "Jump to details" in result.text
    assert "fees on this page" in result.text
    assert "#details" not in result.text
    assert "[Jump to details]" not in result.text  # no leftover link markup around the label


@pytest.mark.parametrize(
    "href", ["mailto:a@b.org", "tel:+123456", "javascript:void(0)", "ftp://x.org/f"]
)
async def test_non_web_links_are_not_reported(href: str) -> None:
    result = await HtmlExtractor().extract(article(in_prose(f'<a href="{href}">this</a>')))
    assert result.links == ()


async def test_same_page_footnote_markers_do_not_flood_the_links() -> None:
    notes = "".join(f'<sup><a href="#cite_note-{n}">[{n}]</a></sup>' for n in range(1, 40))
    result = await HtmlExtractor().extract(article(f"<p>Established in 1902.{notes}</p>"))

    assert result.links == ()
    assert "cite_note" not in result.text
    assert (
        "Established in 1902.[1][2]" in result.text
    )  # plain markers, no `[\\[1\\]]` link wrapping
