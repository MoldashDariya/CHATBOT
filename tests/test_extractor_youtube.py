import asyncio
import json
import time
from types import SimpleNamespace

import pytest
import requests
from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
)

from bot.errors import ExtractionError, FetchHTTPError
from bot.extractors import youtube as youtube_module
from bot.extractors.base import RawSource
from bot.extractors.youtube import (
    YouTubeExtractor,
    clean_snippet,
    parse_short_description,
    pick_transcript,
)
from bot.pipeline.detector import SourceFormat
from bot.security.safe_fetch import FetchResult

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}"


# --- fakes --------------------------------------------------------------------------------


class FakeTranscript:
    def __init__(self, code: str, *, generated: bool = False, lines: list[str] | None = None):
        self.language_code = code
        self.is_generated = generated
        self._lines = lines if lines is not None else [f"caption in {code}"]

    def fetch(self) -> SimpleNamespace:
        return SimpleNamespace(snippets=[SimpleNamespace(text=line) for line in self._lines])


class FakeApi:
    def __init__(
        self, transcripts: list[FakeTranscript] | None = None, error: Exception | None = None
    ):
        self._transcripts = transcripts or []
        self._error = error
        self.requested: list[str] = []

    def list(self, video_id: str) -> list[FakeTranscript]:
        self.requested.append(video_id)
        if self._error:
            raise self._error
        return self._transcripts


class FakeFetcher:
    """Answers oEmbed and watch-page requests; a value that is an exception is raised."""

    def __init__(self, oembed: object = None, page: object = None):
        self._oembed, self._page = oembed, page
        self.urls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.urls.append(url)
        answer = self._oembed if "/oembed" in url else self._page
        if isinstance(answer, Exception):
            raise answer
        body = (answer or "").encode() if isinstance(answer, str) else b""
        return FetchResult(url=url, status_code=200, content_type="text/html", body=body)


def oembed_json(title: str = "Admissions webinar 2027", author: str = "Example University") -> str:
    return json.dumps({"title": title, "author_name": author})


def watch_page(description: str) -> str:
    player_data = {"videoDetails": {"shortDescription": description, "x": 1}}
    return f"<html>...{json.dumps(player_data)}...</html>"


def make_extractor(
    api: FakeApi, fetcher: FakeFetcher | None = None, **kwargs: object
) -> YouTubeExtractor:
    return YouTubeExtractor(fetcher or FakeFetcher(), api_factory=lambda: api, **kwargs)  # type: ignore[arg-type]


# --- transcript choice --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ([("en", False), ("ru", False)], "ru"),  # preferred order: ru before en
        ([("en", True), ("de", False)], "en"),  # automatic preferred beats manual other
        ([("ru", True), ("en", False)], "en"),  # manual preferred beats automatic preferred
        ([("de", True), ("fr", False)], "fr"),  # none preferred: manual first
        ([("de", True), ("fr", True)], "de"),  # only automatic: first one
        ([("ru-RU", False), ("en", False)], "ru-RU"),  # region variant counts as its base
        ([("EN-gb", False)], "EN-gb"),
        ([("kk", False), ("en", False)], "kk"),
        ([("pt-BR", False), ("en", True)], "en"),
    ],
)
def test_pick_transcript(available: list[tuple[str, bool]], expected: str) -> None:
    transcripts = [FakeTranscript(code, generated=gen) for code, gen in available]
    chosen = pick_transcript(transcripts, ("ru", "kk", "en"))
    assert chosen is not None
    assert chosen.language_code == expected


def test_pick_transcript_without_candidates() -> None:
    assert pick_transcript([], ("en",)) is None


# --- text cleaning ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("[Music]", ""),
        ("[♪♪♪]", ""),
        ("[Музыка] Добрый день", "Добрый день"),
        ("hello [Applause] world", "hello world"),
        (">> Welcome everyone", "Welcome everyone"),
        ("so >> yes", "so yes"),
        ("deadline is [1] November", "deadline is [1] November"),  # bracket with a digit stays
        ("it&#39;s &amp; that", "it's & that"),
        ("tabs\tand\nnewlines\xa0here", "tabs and newlines here"),
        ("♪ la la ♪", "la la"),
    ],
)
def test_clean_snippet(raw: str, cleaned: str) -> None:
    assert clean_snippet(raw) == cleaned


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (
            watch_page("Apply by 1 May.\nDetails: https://example.edu/apply"),
            "Apply by 1 May.\nDetails: https://example.edu/apply",
        ),
        (watch_page('Quote "inside" and & ampersand'), 'Quote "inside" and & ampersand'),
        ('"shortDescription":"caf\\u00e9 \\u0026 more\\nline"', "café & more\nline"),
        ("<html>no player data</html>", ""),
        ('"shortDescription":"broken \\x escape"', ""),
        ("", ""),
    ],
)
def test_parse_short_description(page: str, expected: str) -> None:
    assert parse_short_description(page) == expected


def test_parse_short_description_is_capped() -> None:
    assert len(parse_short_description(watch_page("x" * 20_000))) == 5000


# --- extraction ---------------------------------------------------------------------------


async def test_full_extraction() -> None:
    api = FakeApi(
        [FakeTranscript("en", lines=["Welcome to the webinar", "[Music]", "Deadline is May first"])]
    )
    fetcher = FakeFetcher(
        oembed_json(),
        watch_page("Details and forms: https://example.edu/apply\nFAQ https://example.edu/faq."),
    )

    result = await make_extractor(api, fetcher).extract(RawSource(url=URL))

    assert api.requested == [VIDEO_ID]
    assert result.source_format is SourceFormat.YOUTUBE
    assert result.source_url == URL  # the link the user sent, not a rewritten one
    assert result.title == "Admissions webinar 2027"
    assert result.text == (
        "YouTube video: Admissions webinar 2027\n\n"
        "Channel: Example University\n\n"
        "Description:\nDetails and forms: https://example.edu/apply\nFAQ https://example.edu/faq.\n\n"
        "Transcript (en, manual captions):\nWelcome to the webinar Deadline is May first"
    )
    assert result.links == ("https://example.edu/apply", "https://example.edu/faq")
    assert result.meta == {
        "kind": "video",
        "video_id": VIDEO_ID,
        "channel": "Example University",
        "transcript_language": "en",
        "transcript_kind": "manual",
    }


async def test_automatic_captions_are_labelled_as_such() -> None:
    api = FakeApi([FakeTranscript("ru", generated=True, lines=["добрый день"])])

    result = await make_extractor(api).extract(RawSource(url=URL))

    assert "Transcript (ru, auto-generated captions):" in result.text
    assert result.meta["transcript_kind"] == "auto-generated"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=5",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
    ],
)
async def test_video_id_is_taken_from_any_supported_url_form(url: str) -> None:
    api = FakeApi([FakeTranscript("en")])
    await make_extractor(api).extract(RawSource(url=url))
    assert api.requested == [VIDEO_ID]


@pytest.mark.parametrize(
    "fetcher",
    [
        FakeFetcher(FetchHTTPError(404), FetchHTTPError(500)),
        FakeFetcher("not json at all", "<html/>"),
        FakeFetcher('["a", "list"]', ""),
        FakeFetcher(oembed_json(title="", author=""), ""),
        FakeFetcher(None, None),
    ],
    ids=["http-errors", "garbage-json", "json-list", "empty-fields", "empty-bodies"],
)
async def test_missing_title_and_description_never_fail_the_extraction(
    fetcher: FakeFetcher,
) -> None:
    api = FakeApi([FakeTranscript("en", lines=["the actual content"])])

    result = await make_extractor(api, fetcher).extract(RawSource(url=URL))

    assert result.title is None
    assert result.text == "YouTube video\n\nTranscript (en, manual captions):\nthe actual content"
    assert result.links == ()


async def test_title_and_description_requests_go_through_the_safe_fetcher_to_youtube_only() -> None:
    fetcher = FakeFetcher(oembed_json(), watch_page("d"))
    await make_extractor(FakeApi([FakeTranscript("en")]), fetcher).extract(RawSource(url=URL))

    assert len(fetcher.urls) == 2
    assert all(u.startswith("https://www.youtube.com/") for u in fetcher.urls)
    assert any(f"watch?v={VIDEO_ID}" in u for u in fetcher.urls)


# --- failures -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TranscriptsDisabled(VIDEO_ID), "no_transcript"),
        (NoTranscriptFound(VIDEO_ID, ["en"], None), "no_transcript"),  # type: ignore[arg-type]
        (VideoUnavailable(VIDEO_ID), "video_unavailable"),
        (VideoUnplayable(VIDEO_ID, "private", []), "video_unavailable"),
        (AgeRestricted(VIDEO_ID), "video_unavailable"),
        (InvalidVideoId(VIDEO_ID), "video_unavailable"),
        (IpBlocked(VIDEO_ID), "youtube_blocked"),
        (RequestBlocked(VIDEO_ID), "youtube_blocked"),
        (YouTubeRequestFailed(VIDEO_ID, requests.HTTPError("503")), "youtube_failed"),
        (requests.ConnectionError("network down"), "youtube_failed"),
        (RuntimeError("anything unexpected"), "youtube_failed"),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
async def test_library_errors_map_to_stable_reasons(error: Exception, reason: str) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await make_extractor(FakeApi(error=error)).extract(RawSource(url=URL))
    assert exc_info.value.reason == reason


async def test_video_without_any_captions() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await make_extractor(FakeApi([])).extract(RawSource(url=URL))
    assert exc_info.value.reason == "no_transcript"


async def test_captions_that_are_only_sound_cues_count_as_no_transcript() -> None:
    api = FakeApi([FakeTranscript("en", lines=["[Music]", "[♪♪♪]", "[Applause]"])])

    with pytest.raises(ExtractionError) as exc_info:
        await make_extractor(api).extract(RawSource(url=URL))
    assert exc_info.value.reason == "no_transcript"


@pytest.mark.parametrize("url", ["https://www.youtube.com/@channel", "https://example.edu/video"])
async def test_non_video_url_is_refused(url: str) -> None:
    api = FakeApi([FakeTranscript("en")])

    with pytest.raises(ExtractionError) as exc_info:
        await make_extractor(api).extract(RawSource(url=url))

    assert exc_info.value.reason == "video_unavailable"
    assert api.requested == []


async def test_slow_caption_download_times_out() -> None:
    class SlowApi(FakeApi):
        def list(self, video_id: str) -> list[FakeTranscript]:
            time.sleep(0.4)
            return super().list(video_id)

    extractor = make_extractor(SlowApi([FakeTranscript("en")]), timeout_s=0.05)

    with pytest.raises(ExtractionError) as exc_info:
        await extractor.extract(RawSource(url=URL))
    assert exc_info.value.reason == "timeout"


async def test_a_hanging_info_request_cannot_block_a_failed_extraction() -> None:
    class HangingFetcher:
        async def fetch(self, url: str) -> FetchResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    extractor = YouTubeExtractor(
        HangingFetcher(),  # type: ignore[arg-type]
        api_factory=lambda: FakeApi(error=TranscriptsDisabled(VIDEO_ID)),  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ExtractionError) as exc_info:
        await asyncio.wait_for(extractor.extract(RawSource(url=URL)), timeout=2)
    assert exc_info.value.reason == "no_transcript"


# --- the HTTP session used for the library ------------------------------------------------


def test_library_requests_get_a_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []

    def record(self: requests.Session, *args: object, **kwargs: object) -> str:
        seen.append(kwargs)
        return "response"

    monkeypatch.setattr(requests.Session, "request", record)
    session = youtube_module._TimeoutSession()

    session.request("GET", "https://www.youtube.com/")
    session.request("GET", "https://www.youtube.com/", timeout=3)

    assert seen[0]["timeout"] == (5, 15)
    assert seen[1]["timeout"] == 3  # an explicit value is respected
