import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once. Never log secrets or full page text."""
    logging.basicConfig(level=level, format=_FORMAT, force=True)
    # httpx logs full request URLs at INFO; keep them out unless debugging.
    logging.getLogger("httpx").setLevel(logging.WARNING if level != "DEBUG" else logging.DEBUG)
