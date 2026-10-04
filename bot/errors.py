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
