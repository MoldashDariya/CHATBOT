"""Domain errors. Each maps to a human-readable message in the handler (task 1.28);
none of them may carry text that is shown to the user verbatim or contains secrets."""


class BotError(Exception):
    """Base class for expected, user-reportable failures."""


class InvalidURLError(BotError):
    """The string is not a usable http(s) URL."""


class BlockedURLError(InvalidURLError):
    """The URL is well-formed but must not be fetched (non-public address, forbidden port)."""


class UnsupportedFormatError(BotError):
    """The source is something the bot cannot read (image, archive, channel page...)."""

    def __init__(self, reason: str = "other") -> None:
        super().__init__(reason)
        self.reason = reason


class LegacyOfficeFormatError(UnsupportedFormatError):
    """Old binary Office file (.doc and friends): the user should send PDF or DOCX instead."""

    def __init__(self) -> None:
        super().__init__("legacy_office")


class ExtractionError(BotError):
    """The source was fetched but its text could not be extracted.

    `reason` is a stable code for the user-facing message (task 1.28): "scanned_pdf",
    "encrypted_pdf", "corrupt", "unsafe_archive", "no_transcript", "video_unavailable",
    "youtube_blocked", "youtube_failed", "timeout".
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TooLittleTextError(BotError):
    """Too little text was extracted to summarise it (JavaScript-rendered page, login or
    paywall, nearly empty file). The LLM is not called in this case."""

    def __init__(self, source_format: str, chars: int, minimum: int) -> None:
        super().__init__(f"{source_format}: {chars} chars, need {minimum}")
        self.source_format = source_format
        self.chars = chars
        self.minimum = minimum


class LLMError(BotError):
    """The language model could not produce a usable summary.

    `reason` is a stable code for the user-facing message (task 1.28): "llm_busy" (rate limit or
    overload, try again later), "llm_unreachable" (network/timeout), "llm_refused" (the model
    declined), "llm_truncated" (answer cut off at the token limit), "llm_invalid" (the answer
    failed validation twice), "llm_too_large" (input exceeds the model's window), "llm_config"
    (bad key, no access to the model: the operator must act), "llm_failed" (anything else).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProcessingTimeoutError(BotError):
    """The whole request (queue, download, reading, analysis) exceeded its time budget."""


class HostNotFoundError(BotError):
    """DNS resolution failed."""


class TooManyRedirectsError(BotError):
    """Redirect chain exceeded the configured limit."""


class FetchHTTPError(BotError):
    """The server answered with a non-success status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FetchTimeoutError(BotError):
    """The whole fetch did not finish within the time budget."""


class FetchFailedError(BotError):
    """Connection-level failure (refused, TLS error, reset...)."""


class ContentTooLargeError(BotError):
    """Response body exceeds the download limit."""
