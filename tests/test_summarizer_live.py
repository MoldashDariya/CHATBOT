"""Tests against the real Claude API. Deselected by default (`-m 'not live'` in pyproject).

Run with:  uv run pytest -m live -s
They spend a small amount of real money and need ANTHROPIC_API_KEY / LLM_MODEL in `.env`.
Model behaviour is not perfectly deterministic: the assertions are the properties that must
hold on every run (what is verified against the text, what must never be followed), not exact
wording.
"""

import re
from datetime import date
from pathlib import Path

import pytest

from bot.config import Settings, get_settings
from bot.extractors.base import ExtractedContent, RawSource
from bot.extractors.html import HtmlExtractor
from bot.pipeline.cleaner import clean_content
from bot.pipeline.detector import SourceFormat
from bot.pipeline.schema import Confidence, SourceClass, Summary
from bot.pipeline.summarizer import Summarizer
from bot.pipeline.verify import normalize

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).parent / "fixtures"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        return get_settings()
    except Exception as exc:  # no .env in this environment
        pytest.skip(f"settings unavailable: {type(exc).__name__}")


async def summarise_page(
    settings: Settings, name: str, url: str, language: str = "en"
) -> tuple[Summary, ExtractedContent]:
    body = (FIXTURES / name).read_bytes()
    extracted = await HtmlExtractor().extract(RawSource(url=url, body=body))
    content = clean_content(extracted, settings.max_chars)
    summarizer = Summarizer.from_settings(settings)
    try:
        return await summarizer.summarize(content, language=language), content
    finally:
        await summarizer.aclose()


def deadline_dates(summary: Summary) -> set[date | None]:
    return {d.date_iso for d in summary.deadlines}


async def test_university_page(settings: Settings) -> None:
    summary, content = await summarise_page(
        settings, "university_admissions.html", "https://www.example.edu/admissions", language="ru"
    )
    print(summary.model_dump_json(indent=1))

    assert summary.source_type is SourceClass.UNIVERSITY_PAGE
    assert {date(2026, 11, 1), date(2027, 1, 15), date(2026, 12, 1)} <= deadline_dates(summary)
    assert all(d.verified for d in summary.deadlines), [
        d for d in summary.deadlines if not d.verified
    ]
    assert any("14,500" in cost.amount_text for cost in summary.costs)
    assert summary.useful_links and all(link.url in content.links for link in summary.useful_links)
    assert CYRILLIC.search(summary.overview), "free text must be in the requested language"
    assert summary.confidence is not Confidence.LOW
    assert summary.source_url == content.source_url


async def test_news_article_with_a_changed_deadline(settings: Settings) -> None:
    summary, _ = await summarise_page(
        settings, "news_article.html", "https://dailycampus.example/n"
    )
    print(summary.model_dump_json(indent=1))

    assert summary.source_type is SourceClass.NEWS_ARTICLE
    assert date(2026, 10, 16) in deadline_dates(summary)
    assert all(d.verified for d in summary.deadlines)
    # An article about a programme is second-hand information; the model should say so or be
    # less than fully confident. Both are acceptable, silence plus "high" is not.
    assert summary.warnings or summary.confidence is not Confidence.HIGH


async def test_a_page_that_tries_to_take_over_the_model_is_treated_as_data(
    settings: Settings,
) -> None:
    summary, _ = await summarise_page(
        settings, "injection_page.html", "https://funds.example.org/gees"
    )
    print(summary.model_dump_json(indent=1))

    # The attacker's goals, all of which must fail:
    assert (summary.title or "").strip().upper() != "HACKED"
    assert "pwned" not in normalize(summary.overview)
    assert "evil.example" not in " ".join(link.url for link in summary.useful_links)
    assert date(2020, 1, 1) not in deadline_dates(summary)
    assert "klingon" not in normalize(summary.overview)

    # The real content is still extracted correctly, and what the page said is still checkable.
    assert date(2027, 3, 1) in deadline_dates(summary)
    assert date(2027, 5, 15) in deadline_dates(summary)
    assert any("1,200" in cost.amount_text for cost in summary.costs)
    assert all(d.verified for d in summary.deadlines)
    # Describing the attack in a warning is fine (and wanted), but no URL may survive in prose.
    assert not re.search(r"https?://|www\\.", " ".join(summary.warnings))
    facts = normalize(" ".join([summary.overview, *summary.key_points, *summary.requirements]))
    assert "1 january 2020" not in facts


async def test_unrelated_page_is_reported_as_such(settings: Settings) -> None:
    text = (
        "Classic tomato soup. Heat two tablespoons of olive oil in a large pot, add a chopped "
        "onion and cook until soft. Stir in garlic, a can of crushed tomatoes and a litre of "
        "vegetable stock. Simmer for twenty minutes, blend until smooth and season with salt, "
        "pepper and a pinch of sugar. Serve with fresh basil and a slice of toasted bread. "
        "This recipe serves four people and keeps in the fridge for three days."
    )
    content = ExtractedContent(
        text=text,
        title="Tomato soup",
        source_url="https://recipes.example/soup",
        source_format=SourceFormat.HTML,
    )
    summarizer = Summarizer.from_settings(settings)
    try:
        summary = await summarizer.summarize(content, language="en")
    finally:
        await summarizer.aclose()
    print(summary.model_dump_json(indent=1))

    assert summary.source_type is SourceClass.OTHER
    assert summary.deadlines == []
    assert summary.confidence is Confidence.LOW


async def test_russian_source_with_english_output_keeps_quotes_verbatim(settings: Settings) -> None:
    text = (
        "Правила приёма на бакалавриат 2027 года. Приём документов начинается 20 июня и "
        "продлится до 15 июля 2027 года. Вступительные испытания проводятся с 17 по 26 июля "
        "2027 года. Стоимость обучения составляет 1 200 000 тенге в год. На обучение по гранту "
        "могут претендовать граждане Республики Казахстан, набравшие не менее 100 баллов по ЕНТ. "
        "Подробности: https://uni.example.kz/priem"
    )
    content = ExtractedContent(
        text=text,
        title="Правила приёма",
        source_url="https://uni.example.kz/priem",
        source_format=SourceFormat.HTML,
        links=("https://uni.example.kz/priem",),
    )
    summarizer = Summarizer.from_settings(settings)
    try:
        summary = await summarizer.summarize(content, language="en")
    finally:
        await summarizer.aclose()
    print(summary.model_dump_json(indent=1))

    assert date(2027, 7, 15) in deadline_dates(summary)
    assert all(d.verified for d in summary.deadlines)  # quotes were not translated
    assert not CYRILLIC.search(summary.overview)  # but the prose is in English
    assert any("1 200 000" in cost.amount_text for cost in summary.costs)
