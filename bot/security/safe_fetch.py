"""HTTP GET with SSRF guard, redirect re-validation, size cap and a total time budget."""

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from bot.config import Settings
from bot.errors import (
    ContentTooLargeError,
    FetchFailedError,
    FetchHTTPError,
    FetchTimeoutError,
    TooManyRedirectsError,
)
from bot.security.url_guard import GuardedTarget, Resolver, guard_url, system_resolve

logger = logging.getLogger(__name__)

USER_AGENT = "AdmissionsSummaryBot/0.1 (Telegram link summarizer)"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_ADDRESSES_TRIED = 3


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects
    status_code: int
    content_type: str  # lower-cased, without parameters; "" if absent
    body: bytes


@dataclass(frozen=True)
class _Hop:
    result: FetchResult | None = None
    redirect_to: str | None = None


class SafeFetcher:
    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_s: float,
        max_redirects: int = 5,
        resolver: Resolver = system_resolve,
    ) -> None:
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self._max_redirects = max_redirects
        self._resolve = resolver

    @classmethod
    def from_settings(cls, settings: Settings) -> "SafeFetcher":
        return cls(
            max_bytes=settings.max_download_mb * 1024 * 1024,
            timeout_s=settings.request_timeout_s,
        )

    async def fetch(self, url: str) -> FetchResult:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._fetch(url)
        except TimeoutError as exc:
            raise FetchTimeoutError(f"no response within {self._timeout_s}s") from exc

    async def _fetch(self, url: str) -> FetchResult:
        # A client per fetch: no cookie jar or connection pool shared between users.
        # trust_env=False: proxy variables would send the request somewhere other than the
        # address we validated.
        async with httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=httpx.Timeout(self._timeout_s),
        ) as client:
            current = url
            for _ in range(self._max_redirects + 1):
                # Re-validated on every hop, so a redirect cannot lead to an internal address.
                target = await guard_url(current, self._resolve)
                hop = await self._get_hop(client, target)
                if hop.result is not None:
                    return hop.result
                assert hop.redirect_to is not None
                current = hop.redirect_to
        raise TooManyRedirectsError(f"more than {self._max_redirects} redirects")

    async def _get_hop(self, client: httpx.AsyncClient, target: GuardedTarget) -> _Hop:
        last_error: Exception | None = None
        for address in target.addresses[:MAX_ADDRESSES_TRIED]:
            try:
                # Connect to the validated IP; Host and SNI keep virtual hosting and
                # certificate verification working for the original hostname.
                async with client.stream(
                    "GET",
                    target.pinned_url(address),
                    headers={"Host": target.host_header},
                    extensions={"sni_hostname": target.host},
                ) as response:
                    return await self._handle_response(target, response)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                logger.info("Connect to %s failed (%s), trying next address", target.host, exc)
            except httpx.TimeoutException as exc:
                raise FetchTimeoutError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise FetchFailedError(type(exc).__name__) from exc
        raise FetchFailedError("could not connect") from last_error

    async def _handle_response(self, target: GuardedTarget, response: httpx.Response) -> _Hop:
        status = response.status_code
        if status in REDIRECT_STATUSES:
            location = response.headers.get("location")
            if not location:
                raise FetchHTTPError(status)
            return _Hop(redirect_to=urljoin(target.url, location))
        if not 200 <= status < 300:
            raise FetchHTTPError(status)

        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise ContentTooLargeError(f"declared {declared} bytes")

        # Counted after decompression, so a gzip bomb is cut off at the limit as well.
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise ContentTooLargeError(f"more than {self._max_bytes} bytes")
            chunks.append(chunk)

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return _Hop(
            result=FetchResult(
                url=target.url,
                status_code=status,
                content_type=content_type,
                body=b"".join(chunks),
            )
        )
