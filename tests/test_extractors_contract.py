"""Invariants every extractor must satisfy, whatever the format (task 1.15)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import Settings
from bot.extractors import ExtractedContent, RawSource, build_extractors
from bot.extractors.base import find_urls
from bot.extractors.youtube import YouTubeExtractor
from bot.pipeline.detector import SourceFormat, detect_from_content
from bot.security.safe_fetch import FetchResult, SafeFetcher
from tests.docxgen import scholarship_docx
from tests.pdfgen import PageSpec, make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"


class StubFetcher:
    """Stands in for SafeFetcher on the YouTube info requests."""

    async def fetch(self, url: str) -> FetchResult:
        body = (
            b'{"title": "Webinar", "author_name": "Example University"}'
            if "/oembed" in url
            else (b'<html>"shortDescription":"Forms: https://example.edu/forms"</html>')
        )
        return FetchResult(url=url, status_code=200, content_type="text/html", body=body)


class OneTranscriptApi:
    def list(self, video_id: str) -> list[object]:
        snippets = [SimpleNamespace(text="Apply before the first of May")]
        transcript = SimpleNamespace(
            language_code="en",
            is_generated=False,
            fetch=lambda: SimpleNamespace(snippets=snippets),
        )
        return [transcript]


def pdf_bytes() -> bytes:
    page = PageSpec(body=["Deadline: 1 May 2027.", "Apply at https://example.edu/apply today."])
    return make_pdf([page], title="Rules")


CASES: dict[str, tuple[SourceFormat, RawSource]] = {
    "html": (
        SourceFormat.HTML,
        RawSource(
            url="https://www.example.edu/admissions/undergraduate",
            body=(FIXTURES / "university_admissions.html").read_bytes(),
            content_type="text/html",
        ),
    ),
    "pdf": (
        SourceFormat.PDF,
        RawSource(
            url="https://example.edu/rules.pdf", body=pdf_bytes(), content_type="application/pdf"
        ),
    ),
    "docx": (
        SourceFormat.DOCX,
        RawSource(url="https://example.gov/guide.docx", body=scholarship_docx()),
    ),
    "youtube": (SourceFormat.YOUTUBE, RawSource(url=VIDEO_URL)),
}


@pytest.fixture
def extractors() -> dict[SourceFormat, object]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="1234567890:" + "A" * 35,  # type: ignore[arg-type]
        anthropic_api_key="x",  # type: ignore[arg-type]
        llm_model="m",
    )
    built = build_extractors(settings, SafeFetcher(max_bytes=1024, timeout_s=1))
    built[SourceFormat.YOUTUBE] = YouTubeExtractor(
        StubFetcher(),  # type: ignore[arg-type]
        api_factory=OneTranscriptApi,  # type: ignore[arg-type]
    )
    return built  # type: ignore[return-value]


async def run(
    extractors: dict[SourceFormat, object], name: str
) -> tuple[SourceFormat, ExtractedContent]:
    expected_format, source = CASES[name]
    result = await extractors[expected_format].extract(source)  # type: ignore[attr-defined]
    return expected_format, result


@pytest.mark.parametrize("name", CASES)
async def test_result_shape(extractors: dict[SourceFormat, object], name: str) -> None:
    expected_format, result = await run(extractors, name)

    assert isinstance(result, ExtractedContent)
    assert result.source_format is expected_format
    assert result.source_url == CASES[name][1].url
    assert isinstance(result.text, str)
    assert len(result.text) > 50
    assert result.title is None or (isinstance(result.title, str) and result.title.strip())
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in result.meta.items())


@pytest.mark.parametrize("name", CASES)
async def test_links_are_unique_absolute_web_urls_that_occur_in_the_text(
    extractors: dict[SourceFormat, object], name: str
) -> None:
    """The summary may only offer links found in the source; this is what it checks against."""
    _, result = await run(extractors, name)

    assert isinstance(result.links, tuple)
    assert len(set(result.links)) == len(result.links)
    for link in result.links:
        assert link.startswith(("http://", "https://"))
        assert link in result.text, f"{link} is reported but absent from the text"


@pytest.mark.parametrize("name", CASES)
async def test_every_url_in_the_text_is_reported(
    extractors: dict[SourceFormat, object], name: str
) -> None:
    _, result = await run(extractors, name)

    assert set(find_urls(result.text)) <= set(result.links)


async def test_each_format_has_exactly_one_extractor() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="1234567890:" + "A" * 35,  # type: ignore[arg-type]
        anthropic_api_key="x",  # type: ignore[arg-type]
        llm_model="m",
        max_pdf_pages=7,
    )
    built = build_extractors(settings, SafeFetcher(max_bytes=1024, timeout_s=1))

    assert set(built) == set(SourceFormat)
    assert built[SourceFormat.PDF]._max_pages == 7  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", ["html", "pdf", "docx"])
async def test_extractor_input_is_what_the_detector_accepts(name: str) -> None:
    """The detector and the extractors must agree on what each format is."""
    expected_format, source = CASES[name]
    assert detect_from_content(source.content_type, source.body) is expected_format
