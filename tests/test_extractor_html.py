from pathlib import Path

import pytest

from bot.errors import ExtractionError
from bot.extractors import html as html_module
from bot.extractors.base import RawSource
from bot.extractors.html import HtmlExtractor
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
