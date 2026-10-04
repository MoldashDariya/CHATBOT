"""Errors -> what the user is told, and what the operator gets in the log.

Rules: the user never sees an exception name, a stack trace, a status line from a server, a
file path or anything about our internals (a blocked internal address, a bad API key). The
log gets the detail, at a level that matches who has to act.
"""

import logging

from bot.errors import (
    BlockedURLError,
    BotError,
    ContentTooLargeError,
    ExtractionError,
    FetchFailedError,
    FetchHTTPError,
    FetchTimeoutError,
    HostNotFoundError,
    InvalidURLError,
    LLMError,
    ProcessingTimeoutError,
    TooLittleTextError,
    TooManyRedirectsError,
    UnsupportedFormatError,
)
from bot.render.texts import text

logger = logging.getLogger(__name__)

_HTTP_NEEDS_ACCESS = {401, 402, 403, 407, 451}
_HTTP_GONE = {404, 410}
_HTTP_BUSY = {429}

_UNSUPPORTED_KEYS = {
    "legacy_office": "err_legacy_office",
    "youtube_not_a_video": "err_not_a_video",
    "empty": "err_empty",
    "archive": "err_archive",
}
_EXTRACTION_KEYS = {
    "scanned_pdf": "err_scanned_pdf",
    "encrypted_pdf": "err_encrypted_pdf",
    "corrupt": "err_corrupt",
    "unsafe_archive": "err_unsafe_archive",
    "timeout": "err_extract_timeout",
    "no_transcript": "err_no_transcript",
    "video_unavailable": "err_video_unavailable",
    "youtube_blocked": "err_youtube_blocked",
    "youtube_failed": "err_youtube_failed",
}
_THIN_KEYS = {"html": "err_thin_html", "pdf": "err_thin_file", "docx": "err_thin_file"}
_LLM_KEYS = {
    "llm_busy": "err_llm_busy",
    "llm_unreachable": "err_llm_unreachable",
    "llm_refused": "err_llm_refused",
    "llm_too_large": "err_llm_too_large",
    "llm_truncated": "err_llm_retry",
    "llm_invalid": "err_llm_retry",
    "llm_failed": "err_llm_retry",
    "llm_config": "err_service",  # the operator's problem; the user gets a neutral message
}


def describe_error(exc: BaseException, language: str, *, max_download_mb: int = 15) -> str:
    """The message for the user, in `language`. Pure: no logging, no exception details."""
    key, values = _classify(exc, max_download_mb)
    return text(language, key, **values)


def handle_error(exc: BaseException, language: str, *, max_download_mb: int = 15) -> str:
    """Log `exc` at the right level and return the message for the user."""
    _log(exc)
    return describe_error(exc, language, max_download_mb=max_download_mb)


def _classify(exc: BaseException, max_download_mb: int) -> tuple[str, dict[str, object]]:
    # BlockedURLError is an InvalidURLError and deliberately says the same thing: the user is
    # never told why an address was refused.
    match exc:
        case BlockedURLError() | InvalidURLError():
            return "err_bad_url", {}
        case HostNotFoundError():
            return "err_host", {}
        case FetchHTTPError(status_code=code):
            return _http_key(code), {"code": code}
        case FetchTimeoutError():
            return "err_timeout", {}
        case ProcessingTimeoutError():
            return "err_processing_timeout", {}
        case FetchFailedError():
            return "err_connect", {}
        case TooManyRedirectsError():
            return "err_redirects", {}
        case ContentTooLargeError():
            return "err_too_large", {"mb": max_download_mb}
        case UnsupportedFormatError(reason=reason):  # includes LegacyOfficeFormatError
            return _UNSUPPORTED_KEYS.get(reason, "err_unsupported"), {}
        case ExtractionError(reason=reason):
            return _EXTRACTION_KEYS.get(reason, "err_corrupt"), {}
        case TooLittleTextError(source_format=source_format):
            return _THIN_KEYS.get(str(source_format), "err_thin_video"), {}
        case LLMError(reason=reason):
            return _LLM_KEYS.get(reason, "err_llm_retry"), {}
        case _:
            return "err_generic", {}


def _http_key(code: int) -> str:
    if code in _HTTP_NEEDS_ACCESS:
        return "err_http_auth"
    if code in _HTTP_GONE:
        return "err_http_gone"
    if code in _HTTP_BUSY:
        return "err_http_busy"
    if code >= 500:
        return "err_http_server"
    return "err_http_other"


def _log(exc: BaseException) -> None:
    if not isinstance(exc, BotError):
        logger.error("Unexpected error", exc_info=exc)  # a bug: the full traceback goes here
    elif isinstance(exc, LLMError) and exc.reason in {"llm_config", "llm_failed"}:
        logger.error("LLM problem needs the operator: %s", exc.reason)
    else:
        # Expected, user-caused or transient. The URL is deliberately not logged.
        logger.info("Request failed: %s %s", type(exc).__name__, getattr(exc, "reason", ""))
