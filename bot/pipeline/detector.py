"""Source format detection, cheapest signal first (see ARCHITECTURE.md).

1. `detect_from_url`: no download needed (YouTube videos) or no download wanted (legacy .doc).
2. `detect_from_content`: the downloaded bytes decide. Neither the URL extension nor the
   Content-Type header is trusted on its own: servers label files `application/octet-stream`,
   and a ".pdf" link often returns an HTML login or error page.

The result is the *format* the extractors need. The semantic class of an HTML page
(university / scholarship / news ...) is decided later from its text.
"""

import io
import zipfile
from enum import StrEnum
from urllib.parse import urlsplit

from bot.errors import LegacyOfficeFormatError, UnsupportedFormatError
from bot.security.safe_fetch import FetchResult
from bot.urls import is_youtube_host, youtube_video_id


class SourceFormat(StrEnum):
    YOUTUBE = "youtube"
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"


_PDF_MAGIC = b"%PDF-"
_PDF_LENIENT_WINDOW = 1024  # the PDF spec allows junk before the header within the first KiB
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")  # legacy .doc/.xls/.ppt container
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_HTML_MARKERS = ("<!doctype html", "<html", "<head", "<body")
_HTML_SNIFF_WINDOW = 1024
_BOM = b"\xef\xbb\xbf"
_WHITESPACE = b" \t\r\n\x00"


def detect_from_url(url: str) -> SourceFormat | None:
    """Decide by URL alone when possible. None means: download it and look at the content."""
    parts = urlsplit(url)
    host = parts.hostname or ""

    if is_youtube_host(host):
        if youtube_video_id(url) is None:
            # A channel, playlist or search page: fetching it would only give consent-wall junk.
            raise UnsupportedFormatError("youtube_not_a_video")
        return SourceFormat.YOUTUBE

    if parts.path.lower().endswith(".doc"):
        raise LegacyOfficeFormatError

    return None


def check_filename(filename: str) -> None:
    """The file-upload counterpart of the URL pre-check: refuse a legacy .doc by its name,
    before the file is even read."""
    if filename.strip().lower().endswith(".doc"):
        raise LegacyOfficeFormatError


def detect_from_content(content_type: str, body: bytes) -> SourceFormat:
    """Decide from the downloaded bytes. `content_type` is lower-cased, without parameters."""
    if not body.lstrip(_WHITESPACE):  # lstrip copies only if something was stripped
        raise UnsupportedFormatError("empty")

    if _is_pdf(content_type, body):
        return SourceFormat.PDF
    if body.startswith(_OLE_MAGIC):
        raise LegacyOfficeFormatError
    if body.startswith(_ZIP_MAGIC):
        if _is_docx(body):
            return SourceFormat.DOCX
        raise UnsupportedFormatError("archive")  # xlsx, pptx, plain zip ...
    if content_type in _HTML_CONTENT_TYPES or _looks_like_html(body):
        return SourceFormat.HTML
    raise UnsupportedFormatError("other")


def detect_response(result: FetchResult) -> SourceFormat:
    """Detection for a finished fetch, taking the *final* URL (after redirects) into account."""
    return detect_from_url(result.url) or detect_from_content(result.content_type, result.body)


def _is_pdf(content_type: str, body: bytes) -> bool:
    # Strict by default so an HTML page that merely mentions "%PDF-" is not taken for a PDF;
    # lenient only when the server itself says it is one.
    stripped = body[:_PDF_LENIENT_WINDOW].lstrip(_BOM + _WHITESPACE)
    if stripped.startswith(_PDF_MAGIC):
        return True
    return content_type == "application/pdf" and _PDF_MAGIC in body[:_PDF_LENIENT_WINDOW]


def _is_docx(body: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _looks_like_html(body: bytes) -> bool:
    head = body[:_HTML_SNIFF_WINDOW].lstrip(_BOM + _WHITESPACE).decode("utf-8", errors="ignore")
    head = head.lower()
    return any(marker in head for marker in _HTML_MARKERS)
