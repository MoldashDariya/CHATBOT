import re
from urllib.parse import urlsplit

# Deliberately loose: anything starting with http(s):// up to whitespace or a closing bracket.
# Real validation (scheme, host, SSRF) happens in security/url_guard.py.
_URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?»”…"


def extract_urls(text: str) -> list[str]:
    """Return unique URLs found in `text`, in order of appearance."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group().rstrip(_TRAILING_PUNCT)
        if urlsplit(url).netloc and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
