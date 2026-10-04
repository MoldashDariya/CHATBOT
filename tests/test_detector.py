import io
import zipfile

import pytest

from bot.errors import LegacyOfficeFormatError, UnsupportedFormatError
from bot.pipeline.detector import (
    SourceFormat,
    detect_from_content,
    detect_from_url,
    detect_response,
)
from bot.security.safe_fetch import FetchResult
from bot.urls import youtube_video_id

VIDEO_ID = "dQw4w9WgXcQ"
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
HTML = (
    b"<!DOCTYPE html><html><head><title>Admissions</title></head>"
    b"<body>Deadline: May 1</body></html>"
)
OLE = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 512

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<xml/>")
    return buffer.getvalue()


DOCX = make_zip("[Content_Types].xml", "word/document.xml")
XLSX = make_zip("[Content_Types].xml", "xl/workbook.xml")


# --- YouTube ids --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}&t=42s&list=PL123",
        f"https://www.youtube.com/watch?feature=share&v={VIDEO_ID}",
        f"http://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?si=abcDEF&t=10",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}?feature=share",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
        f"HTTPS://WWW.YOUTUBE.COM/watch?v={VIDEO_ID}",
        f"https://www.youtube.com./watch?v={VIDEO_ID}",
    ],
)
def test_youtube_video_urls(url: str) -> None:
    assert youtube_video_id(url) == VIDEO_ID
    assert detect_from_url(url) is SourceFormat.YOUTUBE


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/channel/UC1234567890abcdefghij",
        "https://www.youtube.com/playlist?list=PLabcdefghijk",
        "https://www.youtube.com/results?search_query=admissions",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=",
        "https://www.youtube.com/watch?v=tooShort",
        f"https://www.youtube.com/watch?v={VIDEO_ID}extra",
        "https://www.youtube.com/shorts/",
        "https://youtu.be/",
        "https://youtu.be/not an id!",
    ],
)
def test_youtube_pages_that_are_not_videos_are_unsupported(url: str) -> None:
    assert youtube_video_id(url) is None
    with pytest.raises(UnsupportedFormatError) as exc_info:
        detect_from_url(url)
    assert exc_info.value.reason == "youtube_not_a_video"


@pytest.mark.parametrize(
    "url",
    [
        f"https://evilyoutube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://notyoutu.be/{VIDEO_ID}",
        f"https://example.edu/watch?v={VIDEO_ID}",
        f"https://example.edu/redirect?to=https://youtu.be/{VIDEO_ID}",
    ],
)
def test_lookalike_domains_are_not_youtube(url: str) -> None:
    assert youtube_video_id(url) is None
    assert detect_from_url(url) is None


# --- URL pre-checks -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.edu/rules.doc",
        "https://example.edu/RULES.DOC?download=1",
        "http://a.edu/x.Doc#p",
    ],
)
def test_legacy_doc_is_refused_before_downloading(url: str) -> None:
    with pytest.raises(LegacyOfficeFormatError) as exc_info:
        detect_from_url(url)
    assert exc_info.value.reason == "legacy_office"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.edu/",
        "https://example.edu/admissions",
        "https://example.edu/rules.docx",
        "https://example.edu/rules.pdf",
        "https://example.edu/page?file=old.doc",  # only the path counts
        "https://example.edu/doc",
        "https://example.edu/handbook.document",
    ],
)
def test_other_urls_are_left_to_content_detection(url: str) -> None:
    assert detect_from_url(url) is None


# --- content: PDF -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "application/octet-stream", "", "text/html"]
)
def test_pdf_is_recognised_by_magic_bytes_whatever_the_header_says(content_type: str) -> None:
    assert detect_from_content(content_type, PDF) is SourceFormat.PDF


def test_pdf_with_leading_whitespace_or_bom() -> None:
    assert detect_from_content("", b"\xef\xbb\xbf\r\n  " + PDF) is SourceFormat.PDF


def test_pdf_with_junk_before_header_needs_the_pdf_content_type() -> None:
    junk_pdf = b"GARBAGE-FROM-A-PROXY\n" + PDF
    assert detect_from_content("application/pdf", junk_pdf) is SourceFormat.PDF
    with pytest.raises(UnsupportedFormatError):
        detect_from_content("application/octet-stream", junk_pdf)


def test_pdf_header_beyond_the_first_kib_is_not_a_pdf() -> None:
    late = b"x" * 2000 + PDF
    with pytest.raises(UnsupportedFormatError):
        detect_from_content("application/pdf", late)


def test_html_page_that_merely_mentions_the_pdf_header_stays_html() -> None:
    page = (
        b"<html><head><meta name='note' content='files start with %PDF-1.7'></head>"
        b"<body>x</body></html>"
    )
    assert detect_from_content("text/html", page) is SourceFormat.HTML


# --- content: DOCX / Office ---------------------------------------------------------------


def test_docx_is_recognised_by_its_structure() -> None:
    assert detect_from_content(DOCX_CONTENT_TYPE, DOCX) is SourceFormat.DOCX
    # Servers often send a generic type; the archive content decides.
    assert detect_from_content("application/octet-stream", DOCX) is SourceFormat.DOCX
    assert detect_from_content("application/zip", DOCX) is SourceFormat.DOCX


@pytest.mark.parametrize(
    "body",
    [XLSX, make_zip("a.txt", "b.txt"), b"PK\x03\x04" + b"\x00" * 64, b"PK\x03\x04"],
    ids=["xlsx", "plain-zip", "corrupt-zip", "truncated-zip"],
)
def test_other_zip_files_are_unsupported(body: bytes) -> None:
    with pytest.raises(UnsupportedFormatError) as exc_info:
        detect_from_content("application/zip", body)
    assert exc_info.value.reason == "archive"


def test_docx_content_type_does_not_make_html_a_docx() -> None:
    # A login page served with the DOCX content type is still just HTML.
    assert detect_from_content(DOCX_CONTENT_TYPE, HTML) is SourceFormat.HTML


@pytest.mark.parametrize("content_type", ["application/msword", "application/octet-stream", ""])
def test_legacy_ole_documents_are_refused_by_magic_bytes(content_type: str) -> None:
    with pytest.raises(LegacyOfficeFormatError):
        detect_from_content(content_type, OLE)


# --- content: HTML ------------------------------------------------------------------------


@pytest.mark.parametrize("content_type", ["text/html", "application/xhtml+xml"])
def test_html_by_content_type(content_type: str) -> None:
    assert (
        detect_from_content(content_type, b"<p>fragment without document tags</p>")
        is SourceFormat.HTML
    )


@pytest.mark.parametrize(
    "body",
    [
        HTML,
        b"\xef\xbb\xbf\r\n\r\n" + HTML,
        b"<HTML><HEAD></HEAD><BODY>x</BODY></HTML>",
        b"<!DOCTYPE HTML PUBLIC '-//W3C//DTD HTML 4.01//EN'><html>x</html>",
        b"<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><body>x</body></html>",
    ],
)
def test_html_is_sniffed_when_the_header_is_wrong_or_missing(body: bytes) -> None:
    assert detect_from_content("application/octet-stream", body) is SourceFormat.HTML
    assert detect_from_content("", body) is SourceFormat.HTML


def test_html_with_invalid_utf8_still_detected() -> None:
    body = b"<html><body>\xff\xfe\xfd caf\xe9</body></html>"
    assert detect_from_content("application/octet-stream", body) is SourceFormat.HTML


# --- content: everything else -------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32),
        ("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 32),
        ("application/json", b'{"deadline": "2027-05-01"}'),
        ("text/plain", b"Plain text without any markup."),
        ("application/xml", b"<?xml version='1.0'?><feed><entry/></feed>"),
        ("application/octet-stream", bytes(range(256))),
        ("video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32),
    ],
)
def test_unsupported_content(content_type: str, body: bytes) -> None:
    with pytest.raises(UnsupportedFormatError) as exc_info:
        detect_from_content(content_type, body)
    assert exc_info.value.reason == "other"


@pytest.mark.parametrize("body", [b"", b"   ", b"\r\n\t \x00"])
def test_empty_bodies(body: bytes) -> None:
    with pytest.raises(UnsupportedFormatError) as exc_info:
        detect_from_content("text/html", body)
    assert exc_info.value.reason == "empty"


# --- whole fetch results ------------------------------------------------------------------


def result(url: str, content_type: str, body: bytes) -> FetchResult:
    return FetchResult(url=url, status_code=200, content_type=content_type, body=body)


def test_pdf_without_extension_behind_a_download_link() -> None:
    fetched = result("https://example.edu/download?id=3", "application/octet-stream", PDF)
    assert detect_response(fetched) is SourceFormat.PDF


def test_pdf_link_that_returns_an_html_error_page_is_html() -> None:
    fetched = result("https://example.edu/rules.pdf", "text/html", HTML)
    assert detect_response(fetched) is SourceFormat.HTML


def test_redirect_landing_on_a_docx_file() -> None:
    fetched = result("https://cdn.example.org/files/abc123", DOCX_CONTENT_TYPE, DOCX)
    assert detect_response(fetched) is SourceFormat.DOCX


def test_redirect_landing_on_youtube_is_detected_from_the_final_url() -> None:
    fetched = result(f"https://www.youtube.com/watch?v={VIDEO_ID}", "text/html", HTML)
    assert detect_response(fetched) is SourceFormat.YOUTUBE


def test_redirect_landing_on_legacy_doc_is_refused() -> None:
    fetched = result("https://cdn.example.org/rules.doc", "application/msword", OLE)
    with pytest.raises(LegacyOfficeFormatError):
        detect_response(fetched)


def test_regular_html_page() -> None:
    assert (
        detect_response(result("https://example.edu/admissions", "text/html", HTML))
        is SourceFormat.HTML
    )
