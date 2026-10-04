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


_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "igshid", "_ga", "_gl"}
)


def cache_key(url: str) -> str | None:
    """What identifies the page behind `url` for the answer cache, or None if it cannot be keyed.

    Links that differ only in tracking parameters (`utm_*`, `fbclid`...), a `#fragment`, the case
    of the host or a default port are the same page. A YouTube video is its 11-character id,
    whichever way it was linked (`youtu.be/ID`, `watch?v=ID&t=30s`). Nothing else is merged: a
    different path, the `www.` prefix, `http` and `https`, or a different query stay different
    pages, because merging pages that are not the same would serve the wrong summary.
    """
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host or parts.username or parts.password:
        return None  # not ours to key: the pipeline refuses such links itself

    video = youtube_video_id(url)
    if video:
        return f"youtube:{video}"

    shown_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    netloc = shown_host if port in (None, default_port) else f"{shown_host}:{port}"
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and not _is_tracking_parameter(pair.split("=", 1)[0])
    )
    return f"{scheme}://{netloc}{parts.path or '/'}" + (f"?{query}" if query else "")


def _is_tracking_parameter(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in _TRACKING_PARAMS


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
