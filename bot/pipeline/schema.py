"""Result models.

Two layers, on purpose:
- `LLMSummary` is exactly what the model is asked to return (its JSON schema is sent with the
  request). It holds no `source_url` and no verification results: those are not the model's to
  decide ("source_url is filled in by code, never generated" in ARCHITECTURE.md).
- `Summary` is what the rest of the bot works with: the model's answer after the code checked
  it against the source text, plus facts only the code knows.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from bot.pipeline.detector import SourceFormat


class SourceClass(StrEnum):
    """What the page *is* (distinct from `SourceFormat`, which is the file type)."""

    UNIVERSITY_PAGE = "university_page"
    SCHOLARSHIP = "scholarship"
    NEWS_ARTICLE = "news_article"
    PUBLICATION = "publication"
    GENERAL_SITE = "general_site"
    OTHER = "other"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"high": 2, "medium": 1, "low": 0}[self.value]


# --- what the model returns ---------------------------------------------------------------


class LLMDeadline(BaseModel):
    label: str  # what the deadline is for, e.g. "Early round"
    date_iso: str | None  # YYYY-MM-DD, only when the full date (with year) is in the source
    date_text: str  # the date exactly as written in the source
    quote: str  # a verbatim sentence or table row from the source that states it


class LLMCost(BaseModel):
    item: str  # tuition, application fee, stipend, what is covered...
    amount_text: str  # amount and period exactly as written


class LLMLink(BaseModel):
    title: str
    url: str


class LLMSummary(BaseModel):
    source_type: SourceClass
    title: str | None
    organization: str | None
    overview: str
    deadlines: list[LLMDeadline]
    key_points: list[str]
    eligibility: list[str]
    costs: list[LLMCost]
    requirements: list[str]
    useful_links: list[LLMLink]
    warnings: list[str]
    confidence: Confidence


# --- what the bot works with --------------------------------------------------------------


class Deadline(BaseModel):
    label: str
    date_iso: date | None
    date_text: str
    quote: str
    verified: bool  # the quote and the date really occur in the source text
    possibly_passed: bool  # date_iso is earlier than today


class Summary(BaseModel):
    source_url: str  # set by code
    source_format: SourceFormat
    source_type: SourceClass
    language: str  # language of the free-text fields ("ru", "en", "kk")
    title: str | None
    organization: str | None
    overview: str
    deadlines: list[Deadline]
    key_points: list[str]
    eligibility: list[str]
    costs: list[LLMCost]
    requirements: list[str]
    useful_links: list[LLMLink]  # only URLs that occur in the source
    warnings: list[str]  # the model's own remarks, in `language`
    confidence: Confidence
    # Facts for the renderer to turn into localized notes (kept out of `warnings` so they can
    # be translated and so the model cannot suppress them):
    dropped_links: int = 0  # suggested links that do not occur in the source
    truncated: bool = False  # the source text was cut at the processing limit
    pages_read: int | None = None  # PDF: partial coverage when pages_read < pages_total
    pages_total: int | None = None
    extra_sources: list[str] = []  # linked pages that were read in addition to the source
    cached_minutes_ago: int | None = None  # set when the answer comes from the cache

    @property
    def partial_coverage(self) -> bool:
        pages_cut = (
            self.pages_read is not None
            and self.pages_total is not None
            and self.pages_read < self.pages_total
        )
        return self.truncated or pages_cut
