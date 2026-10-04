"""The whole pipeline with only the network and the model replaced.

Real: URL guard, fetcher, detector, extractors, cleaner, summarizer, verification.
Fake: HTTP (respx) and the Anthropic client.
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
import respx

from bot.errors import (
    ExtractionError,
    FetchHTTPError,
    HostNotFoundError,
    InvalidURLError,
    LegacyOfficeFormatError,
    LLMError,
    ProcessingTimeoutError,
    TooLittleTextError,
    UnsupportedFormatError,
)
from bot.extractors.base import RawSource
from bot.extractors.docx import DocxExtractor
from bot.extractors.html import HtmlExtractor
from bot.extractors.pdf import PdfExtractor
from bot.extractors.youtube import YouTubeExtractor
from bot.pipeline.detector import SourceFormat
from bot.pipeline.orchestrator import Orchestrator, Stage
from bot.pipeline.schema import SourceClass
from bot.pipeline.summarizer import Summarizer
from bot.security.safe_fetch import SafeFetcher
from tests.docxgen import scholarship_docx
from tests.fakes import FakeClient, FakeMessages, api_error, llm_answer, reply
from tests.pdfgen import PageSpec, make_pdf

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 4)
SITE_IP = "93.184.216.34"
YOUTUBE_IP = "142.250.80.46"
HOSTS = {
    "www.example.edu": [SITE_IP],
    "files.example.edu": [SITE_IP],
    "short.example": [SITE_IP],
    "www.youtube.com": [YOUTUBE_IP],
}
UNIVERSITY_URL = "https://www.example.edu/admissions/undergraduate"


async def resolve(host: str, port: int) -> list[str]:
    try:
        return HOSTS[host]
    except KeyError:
        raise HostNotFoundError(host) from None


class OneVideoApi:
    def list(self, video_id: str) -> list[Any]:
        lines = [
            "Welcome to the admissions webinar for the next intake",
            "Applications close on the first of May",
            "You will need your transcript, two letters of recommendation and a language "
            "certificate",
            "Our admissions office answers questions by email during working hours every week",
        ]
        snippets = [SimpleNamespace(text=line) for line in lines]
        transcript = SimpleNamespace(
            language_code="en",
            is_generated=False,
            fetch=lambda: SimpleNamespace(snippets=snippets),
        )
        return [transcript]


def build(
    *outcomes: Any,
    max_concurrent: int = 4,
    timeout_s: float = 10.0,
    messages: FakeMessages | None = None,
) -> tuple[Orchestrator, FakeClient]:
    client = FakeClient(*outcomes)
    if messages is not None:
        client.messages = messages
    fetcher = SafeFetcher(max_bytes=15 * 1024 * 1024, timeout_s=5, resolver=resolve)
    extractors = {
        SourceFormat.HTML: HtmlExtractor(),
        SourceFormat.PDF: PdfExtractor(max_pages=80),
        SourceFormat.DOCX: DocxExtractor(),
        SourceFormat.YOUTUBE: YouTubeExtractor(fetcher, api_factory=OneVideoApi),  # type: ignore[arg-type]
    }
    summarizer = Summarizer(
        client,  # type: ignore[arg-type]
        model="test-model",
        effort="medium",
        today=lambda: TODAY,
    )
    pipeline = Orchestrator(
        fetcher=fetcher,
        extractors=extractors,  # type: ignore[arg-type]
        summarizer=summarizer,
        max_chars=120_000,
        max_concurrent=max_concurrent,
        timeout_s=timeout_s,
    )
    return pipeline, client


def serve_university(router: respx.MockRouter, path: str = "/admissions/undergraduate") -> None:
    router.get(f"https://{SITE_IP}{path}").respond(
        200, content=(FIXTURES / "university_admissions.html").read_bytes(),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )  # fmt: skip


class Recorder:
    def __init__(self) -> None:
        self.stages: list[Stage] = []

    async def __call__(self, stage: Stage) -> None:
        self.stages.append(stage)


# --- the happy paths ----------------------------------------------------------------------


async def test_a_web_page_becomes_a_verified_summary() -> None:
    pipeline, _ = build(reply())
    stages = Recorder()

    with respx.mock(assert_all_called=True) as router:
        serve_university(router)
        summary = await pipeline.run(UNIVERSITY_URL, language="ru", on_stage=stages)

    assert summary.source_format is SourceFormat.HTML
    assert summary.source_type is SourceClass.UNIVERSITY_PAGE
    assert summary.language == "ru"
    assert summary.source_url == UNIVERSITY_URL
    assert [d.verified for d in summary.deadlines] == [True, True]
    assert [link.url for link in summary.useful_links] == ["https://www.example.edu/apply/online"]
    assert stages.stages == [Stage.FETCHING, Stage.READING, Stage.ANALYZING]


async def test_the_model_sees_the_cleaned_text_not_the_raw_page() -> None:
    pipeline, client = build(reply())

    with respx.mock() as router:
        serve_university(router)
        await pipeline.run(UNIVERSITY_URL, language="en")

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "| Early round | 1 November 2026 |" in prompt  # the content is there
    for noise in (
        "Accept all cookies",
        "Student login",
        "Win a free laptop",
        "All rights reserved",
        "<nav",
    ):
        assert noise not in prompt
    assert "<script" not in prompt and "dataLayer" not in prompt
    # The source wraps this sentence over indented lines; only the cleaner joins it again.
    assert "The application fee is 75 EUR and is non-refundable." in prompt
    assert "\n         " not in prompt  # no leftover source indentation


async def test_the_source_shown_is_the_users_own_link_even_after_redirects() -> None:
    pipeline, _ = build(reply())
    sent = "https://short.example/go?utm_source=telegram"

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/go", params={"utm_source": "telegram"}).respond(
            301, headers={"Location": UNIVERSITY_URL}
        )
        serve_university(router)
        summary = await pipeline.run(sent, language="en")

    assert summary.source_url == sent


async def test_a_pdf_behind_a_link_without_an_extension() -> None:
    page = PageSpec(
        body=[
            "Admissions rules 2027",
            "Early round: applications close on 1 November 2026.",
            "x" * 400,
        ]
    )
    pdf = make_pdf([page], title="Rules")
    answer = llm_answer(
        deadlines=[
            {
                "label": "Early round",
                "date_iso": "2026-11-01",
                "date_text": "1 November 2026",
                "quote": "Early round: applications close on 1 November 2026.",
            }
        ],
        useful_links=[],
    )
    pipeline, _ = build(reply(answer))

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/download").respond(
            200, content=pdf, headers={"Content-Type": "application/octet-stream"}
        )
        summary = await pipeline.run("https://files.example.edu/download?id=7", language="en")

    assert summary.source_format is SourceFormat.PDF
    assert [d.verified for d in summary.deadlines] == [True]
    assert summary.pages_read == summary.pages_total == 1


async def test_a_docx_file() -> None:
    answer = llm_answer(
        deadlines=[
            {
                "label": "Documents",
                "date_iso": "2027-03-01",
                "date_text": "1 March 2027",
                "quote": "Documents | 1 March 2027 | Online upload",
            }
        ],
        useful_links=[{"title": "Apply", "url": "https://scholarships.example.gov/apply"}],
    )
    pipeline, _ = build(reply(answer))

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/guide.docx").respond(200, content=scholarship_docx())
        summary = await pipeline.run("https://files.example.edu/guide.docx", language="en")

    assert summary.source_format is SourceFormat.DOCX
    assert summary.deadlines[0].verified
    assert [link.url for link in summary.useful_links] == ["https://scholarships.example.gov/apply"]


async def test_a_youtube_video_is_never_downloaded_as_a_page() -> None:
    answer = llm_answer(
        deadlines=[
            {
                "label": "Applications close",
                "date_iso": None,
                "date_text": "the first of May",
                "quote": "Applications close on the first of May",
            }
        ],
        useful_links=[],
    )
    pipeline, client = build(reply(answer))
    stages = Recorder()
    oembed = json.dumps({"title": "Admissions webinar", "author_name": "Example University"})

    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{YOUTUBE_IP}/oembed").respond(200, text=oembed)
        watch = router.get(f"https://{YOUTUBE_IP}/watch").respond(200, text="<html></html>")
        summary = await pipeline.run("https://youtu.be/dQw4w9WgXcQ", language="en", on_stage=stages)

    assert summary.source_format is SourceFormat.YOUTUBE
    assert summary.deadlines[0].verified
    assert stages.stages == [Stage.READING, Stage.ANALYZING]  # nothing to fetch up front
    assert "Transcript (en, manual captions)" in client.messages.calls[0]["messages"][0]["content"]
    assert "youtu.be" not in str(watch.calls)  # only the canonical watch page was asked for


async def test_process_runs_the_part_after_the_download_on_its_own() -> None:
    pipeline, _ = build(reply())
    body = (FIXTURES / "university_admissions.html").read_bytes()

    # No respx and no route: any attempt to fetch would fail the test.
    summary = await pipeline.process(
        RawSource(url=UNIVERSITY_URL, body=body), SourceFormat.HTML, language="en"
    )

    assert summary.source_url == UNIVERSITY_URL
    assert all(d.verified for d in summary.deadlines)


# --- failures stop early, before the model costs anything ---------------------------------


@pytest.mark.parametrize(
    ("url", "error", "reason"),
    [
        ("https://files.example.edu/rules.doc", LegacyOfficeFormatError, "legacy_office"),
        ("https://www.youtube.com/@somechannel", UnsupportedFormatError, "youtube_not_a_video"),
    ],
)
async def test_unsupported_links_are_refused_before_any_request(
    url: str, error: type[Exception], reason: str
) -> None:
    pipeline, client = build()
    stages = Recorder()

    with respx.mock(assert_all_called=False) as router:
        anything = router.route().respond(200)
        with pytest.raises(error) as exc_info:
            await pipeline.run(url, language="en", on_stage=stages)

    assert exc_info.value.reason == reason  # type: ignore[attr-defined]
    assert not anything.called and client.messages.calls == []
    assert stages.stages == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://files.example.edu/x",
    ],
)
async def test_internal_and_non_web_addresses_never_reach_the_network_or_the_model(
    url: str,
) -> None:
    pipeline, client = build()

    with respx.mock(assert_all_called=False) as router:
        anything = router.route().respond(200, text="SECRET")
        with pytest.raises(InvalidURLError) as exc_info:
            await pipeline.run(url, language="en")

    assert isinstance(exc_info.value, InvalidURLError)  # BlockedURLError is one too
    assert not anything.called and client.messages.calls == []


async def test_a_missing_page_is_reported_with_its_status() -> None:
    pipeline, client = build()

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/gone").respond(404, text="not found")
        with pytest.raises(FetchHTTPError) as exc_info:
            await pipeline.run("https://www.example.edu/gone", language="en")

    assert exc_info.value.status_code == 404
    assert client.messages.calls == []


async def test_a_page_without_text_is_refused_and_the_model_is_never_called() -> None:
    pipeline, client = build()
    stages = Recorder()

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/app").respond(
            200,
            content=(FIXTURES / "js_shell.html").read_bytes(),
            headers={"Content-Type": "text/html"},
        )
        with pytest.raises(TooLittleTextError) as exc_info:
            await pipeline.run("https://www.example.edu/app", language="en", on_stage=stages)

    assert exc_info.value.source_format == "html"
    assert client.messages.calls == []
    assert stages.stages == [Stage.FETCHING, Stage.READING]  # never got as far as analysing


async def test_a_scanned_pdf_is_refused_politely() -> None:
    blank = make_pdf([PageSpec(), PageSpec()])
    pipeline, client = build()

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/scan.pdf").respond(200, content=blank)
        with pytest.raises(ExtractionError) as exc_info:
            await pipeline.run("https://files.example.edu/scan.pdf", language="en")

    assert exc_info.value.reason == "scanned_pdf"
    assert client.messages.calls == []


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"),
        (bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600, "application/msword"),
    ],
    ids=["image", "legacy-office-by-content"],
)
async def test_unsupported_content_is_recognised_after_the_download(
    body: bytes, content_type: str
) -> None:
    pipeline, client = build()

    with respx.mock() as router:
        router.get(f"https://{SITE_IP}/file").respond(
            200, content=body, headers={"Content-Type": content_type}
        )
        with pytest.raises(UnsupportedFormatError):
            await pipeline.run("https://files.example.edu/file", language="en")

    assert client.messages.calls == []


async def test_a_model_failure_surfaces_as_an_llm_error() -> None:
    pipeline, _ = build(api_error(anthropic.RateLimitError, 429))

    with respx.mock() as router:
        serve_university(router)
        with pytest.raises(LLMError) as exc_info:
            await pipeline.run(UNIVERSITY_URL, language="en")

    assert exc_info.value.reason == "llm_busy"


# --- time, concurrency, cancellation ------------------------------------------------------


class SlowMessages(FakeMessages):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay
        self.running = 0
        self.peak = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(self.delay)
            return reply()
        finally:
            self.running -= 1


async def test_a_request_that_takes_too_long_is_cut_off_and_frees_its_slot() -> None:
    slow = SlowMessages(delay=5)
    pipeline, _ = build(messages=slow, timeout_s=0.3, max_concurrent=1)

    with respx.mock() as router:
        serve_university(router)
        with pytest.raises(ProcessingTimeoutError):
            await pipeline.run(UNIVERSITY_URL, language="en")

        slow.delay = 0  # the slot must be free again, or this would hang until it times out
        summary = await pipeline.run(UNIVERSITY_URL, language="en")

    assert summary.source_type is SourceClass.UNIVERSITY_PAGE


async def test_waiting_for_a_slot_counts_against_the_time_budget() -> None:
    slow = SlowMessages(delay=0.6)
    pipeline, _ = build(messages=slow, timeout_s=0.4, max_concurrent=1)

    with respx.mock() as router:
        serve_university(router)
        first = asyncio.create_task(pipeline.run(UNIVERSITY_URL, language="en"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(pipeline.run(UNIVERSITY_URL, language="en"))
        results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(r, ProcessingTimeoutError) for r in results)


async def test_no_more_requests_run_at_once_than_the_limit() -> None:
    slow = SlowMessages(delay=0.03)
    pipeline, _ = build(messages=slow, max_concurrent=2)

    with respx.mock() as router:
        serve_university(router)
        results = await asyncio.gather(
            *(pipeline.run(UNIVERSITY_URL, language="en") for _ in range(6))
        )

    assert len(results) == 6
    assert slow.peak == 2


async def test_cancelling_a_request_releases_its_slot() -> None:
    slow = SlowMessages(delay=5)
    pipeline, _ = build(messages=slow, max_concurrent=1)

    with respx.mock() as router:
        serve_university(router)
        task = asyncio.create_task(pipeline.run(UNIVERSITY_URL, language="en"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        slow.delay = 0
        summary = await asyncio.wait_for(pipeline.run(UNIVERSITY_URL, language="en"), timeout=3)

    assert summary.deadlines


async def test_a_failing_progress_callback_does_not_fail_the_request() -> None:
    pipeline, _ = build(reply())

    async def broken(stage: Stage) -> None:
        raise RuntimeError("telegram is down")

    with respx.mock() as router:
        serve_university(router)
        summary = await pipeline.run(UNIVERSITY_URL, language="en", on_stage=broken)

    assert summary.deadlines


async def test_the_pipeline_can_be_reused_for_many_requests() -> None:
    pipeline, client = build(reply(), reply(), reply())

    with respx.mock() as router:
        serve_university(router)
        for _ in range(3):
            await pipeline.run(UNIVERSITY_URL, language="en")

    assert len(client.messages.calls) == 3


async def test_aclose_closes_the_model_client() -> None:
    pipeline, client = build()
    await pipeline.aclose()
    assert client.closed
