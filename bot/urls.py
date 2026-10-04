import re
from urllib.parse import parse_qs, urlsplit

# Deliberately loose: anything starting with http(s):// up to whitespace or a closing bracket.
# Real validation (scheme, host, SSRF) happens in security/url_guard.py.
_URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?»”…"


_YOUTUBE_HOSTS = frozenset({"youtu.be", "youtube.com", "youtube-nocookie.com"})
_YOUTUBE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_PATH_PREFIXES = ("shorts", "embed", "live", "v")


def is_youtube_host(host: str) -> bool:
    """Exact domain or a subdomain of it; `evilyoutube.com` and `youtube.com.evil.com` are not."""
    host = host.lower().rstrip(".")
    return any(host == base or host.endswith(f".{base}") for base in _YOUTUBE_HOSTS)


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id of a YouTube video URL, or None if it is not a video URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not is_youtube_host(host):
        return None

    segments = [s for s in parts.path.split("/") if s]
    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = segments[0] if segments else ""
    elif segments and segments[0] == "watch":
        candidate = parse_qs(parts.query).get("v", [""])[0]
    elif len(segments) >= 2 and segments[0] in _YOUTUBE_PATH_PREFIXES:
        candidate = segments[1]
    else:
        return None
    return candidate if _YOUTUBE_ID_RE.fullmatch(candidate) else None


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
