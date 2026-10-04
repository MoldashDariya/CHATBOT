"""Sample `Summary` objects and helpers for the rendering tests."""

import html
import re
from html.parser import HTMLParser
from typing import Any

from bot.pipeline.detector import SourceFormat
from bot.pipeline.schema import (
    Confidence,
    Deadline,
    LLMCost,
    LLMLink,
    SourceClass,
    Summary,
)

ALLOWED_TAGS = {"b", "i", "a", "code"}  # the subset of HTML that Telegram accepts, and all we use


def make_deadline(**overrides: Any) -> Deadline:
    values: dict[str, Any] = {
        "label": "Early round",
        "date_iso": None,
        "date_text": "1 November 2026",
        "quote": "Early round | 1 November 2026",
        "verified": True,
        "possibly_passed": False,
    }
    values.update(overrides)
    return Deadline(**values)


def make_summary(**overrides: Any) -> Summary:
    """Taken from a real run on the university fixture page, in Russian."""
    values: dict[str, Any] = {
        "source_url": "https://www.example.edu/admissions/undergraduate",
        "source_format": SourceFormat.HTML,
        "source_type": SourceClass.UNIVERSITY_PAGE,
        "language": "ru",
        "title": "Undergraduate Admissions 2027",
        "organization": "Example University",
        "overview": (
            "Страница приёмной комиссии Example University с информацией о подаче заявок на "
            "бакалаврские программы набора 2027 года."
        ),
        "deadlines": [
            make_deadline(),
            make_deadline(
                label="Обычный раунд",
                date_text="15 January 2027",
                quote="Regular round | 15 January 2027",
            ),
            make_deadline(
                label="Подача заявок на стипендию",
                date_text="1 December 2026",
                quote="Scholarship applications | 1 December 2026",
            ),
        ],
        "key_points": [
            "Заявки на набор 2027 года уже открыты",
            "Подача заявки осуществляется через онлайн-портал",
        ],
        "eligibility": [
            "Аттестат о среднем образовании или эквивалент",
            "Для иностранных студентов: IELTS 6.5 или TOEFL 90",
        ],
        "costs": [
            LLMCost(item="Обучение для иностранных студентов", amount_text="14,500 EUR per year"),
            LLMCost(item="Вступительный взнос (невозвратный)", amount_text="75 EUR"),
        ],
        "requirements": ["Заверенная выписка из зачётной ведомости", "Два рекомендательных письма"],
        "useful_links": [
            LLMLink(
                title="Онлайн-портал подачи заявок", url="https://www.example.edu/apply/online"
            ),
            LLMLink(title="FAQ по поступлению", url="https://www.example.edu/faq?lang=en&x=1"),
        ],
        "warnings": [],
        "confidence": Confidence.HIGH,
    }
    values.update(overrides)
    return Summary(**values)


def plain(markup: str) -> str:
    """The text a reader sees: tags removed, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


class _Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}> is not allowed")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(("http://", "https://")):
                self.problems.append(f"href is not http(s): {href!r}")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"</{tag}> does not close {self.stack[-1:]}")
        else:
            self.stack.pop()


def markup_problems(markup: str) -> list[str]:
    """Everything wrong with `markup` as Telegram HTML; an empty list means valid."""
    checker = _Checker()
    checker.feed(markup)
    checker.close()
    leftovers = [f"<{tag}> never closed" for tag in checker.stack]
    # A bare "<" or ">" in text would be read as markup by Telegram.
    stripped = re.sub(r"<[^<>]+>", "", markup)
    stray = ["stray < or >"] if "<" in stripped or ">" in stripped else []
    return [*checker.problems, *leftovers, *stray]
