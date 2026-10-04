import asyncio
import gzip
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from bot.errors import (
    BlockedURLError,
    ContentTooLargeError,
    FetchFailedError,
    FetchHTTPError,
    FetchTimeoutError,
    HostNotFoundError,
    InvalidURLError,
    TooManyRedirectsError,
)
from bot.security.safe_fetch import USER_AGENT, SafeFetcher
from bot.security.url_guard import Resolver

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "151.101.1.69"


def resolver_for(mapping: dict[str, list[str]], calls: list[str] | None = None) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        if calls is not None:
            calls.append(host)
        try:
            return mapping[host]
        except KeyError:
            raise HostNotFoundError(host) from None

    return resolve


def make_fetcher(
    mapping: dict[str, list[str]] | None = None,
    *,
    max_bytes: int = 1024 * 1024,
    timeout_s: float = 5,
    max_redirects: int = 5,
    calls: list[str] | None = None,
) -> SafeFetcher:
    resolver = resolver_for(mapping or {"example.edu": [PUBLIC_IP]}, calls)
    return SafeFetcher(
        max_bytes=max_bytes, timeout_s=timeout_s, max_redirects=max_redirects, resolver=resolver
    )


async def test_fetches_page_through_the_validated_ip() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"http://{PUBLIC_IP}/admissions").respond(
            200, text="<html>hi</html>", headers={"Content-Type": "Text/HTML; charset=utf-8"}
        )

        result = await make_fetcher().fetch("http://example.edu/admissions")

    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.body == b"<html>hi</html>"
    assert result.url == "http://example.edu/admissions"

    request = route.calls.last.request
    assert request.headers["host"] == "example.edu"  # virtual hosting still works
    assert request.extensions["sni_hostname"] == "example.edu"  # TLS verifies the real name
    assert request.headers["user-agent"] == USER_AGENT


async def test_query_string_is_preserved() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"https://{PUBLIC_IP}/p", params={"year": "2027"}).respond(200, text="ok")
        result = await make_fetcher().fetch("https://example.edu/p?year=2027")
    assert result.body == b"ok"


async def test_dns_rebinding_cannot_redirect_the_connection() -> None:
    """The hostname is resolved once; the connection goes to that exact validated IP.

    If the fetcher handed the hostname to httpx, a second (malicious) DNS answer of 127.0.0.1
    would be used for the real connection. Here the resolver would return an internal address
    on every call after the first, and no request may ever target it.
    """
    answers = iter([[PUBLIC_IP], ["127.0.0.1"], ["127.0.0.1"]])

    async def rebinding_resolver(host: str, port: int) -> list[str]:
        return next(answers)

    fetcher = SafeFetcher(max_bytes=1024, timeout_s=5, resolver=rebinding_resolver)
    with respx.mock(assert_all_called=False) as router:
        public = router.get(f"http://{PUBLIC_IP}/").respond(200, text="ok")
        internal = router.route(host="127.0.0.1").respond(200, text="SECRET")

        result = await fetcher.fetch("http://evil.example/")

    assert result.body == b"ok"
    assert public.called
    assert not internal.called


async def test_redirect_to_internal_ip_is_blocked_and_never_requested() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )
        metadata = router.route(host="169.254.169.254").respond(200, text="SECRET")

        with pytest.raises(BlockedURLError):
            await make_fetcher().fetch("http://example.edu/")

    assert not metadata.called


async def test_redirect_to_hostname_resolving_internally_is_blocked() -> None:
    fetcher = make_fetcher({"example.edu": [PUBLIC_IP], "sneaky.example": ["10.0.0.7"]})
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(
            301, headers={"Location": "http://sneaky.example/x"}
        )
        internal = router.route(host="10.0.0.7").respond(200, text="SECRET")

        with pytest.raises(BlockedURLError):
            await fetcher.fetch("http://example.edu/")

    assert not internal.called


@pytest.mark.parametrize("location", ["file:///etc/passwd", "ftp://example.edu/x", "gopher://x/"])
async def test_redirect_to_non_http_scheme_is_rejected(location: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(302, headers={"Location": location})
        with pytest.raises(InvalidURLError):
            await make_fetcher().fetch("http://example.edu/")


async def test_follows_relative_and_cross_host_redirects_revalidating_each_hop() -> None:
    calls: list[str] = []
    fetcher = make_fetcher(
        {"example.edu": [PUBLIC_IP], "cdn.example.org": [OTHER_PUBLIC_IP]}, calls=calls
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(f"http://{PUBLIC_IP}/old").respond(301, headers={"Location": "/new"})
        router.get(f"http://{PUBLIC_IP}/new").respond(
            302, headers={"Location": "https://cdn.example.org/final.pdf"}
        )
        final = router.get(f"https://{OTHER_PUBLIC_IP}/final.pdf").respond(
            200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"}
        )

        result = await fetcher.fetch("http://example.edu/old")

    assert result.url == "https://cdn.example.org/final.pdf"
    assert result.content_type == "application/pdf"
    assert final.calls.last.request.headers["host"] == "cdn.example.org"
    assert calls == ["example.edu", "example.edu", "cdn.example.org"]  # resolved once per hop


async def test_redirect_loop_stops_at_limit() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"http://{PUBLIC_IP}/loop").respond(302, headers={"Location": "/loop"})
        with pytest.raises(TooManyRedirectsError):
            await make_fetcher(max_redirects=5).fetch("http://example.edu/loop")

    assert route.call_count == 6  # the original request plus five redirects


async def test_redirect_without_location_is_an_error() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(302)
        with pytest.raises(FetchHTTPError) as exc_info:
            await make_fetcher().fetch("http://example.edu/")
    assert exc_info.value.status_code == 302


@pytest.mark.parametrize("status", [401, 403, 404, 410, 429, 500, 503])
async def test_error_statuses_raise_with_the_code(status: int) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(status, text="nope")
        with pytest.raises(FetchHTTPError) as exc_info:
            await make_fetcher().fetch("http://example.edu/")
    assert exc_info.value.status_code == status


async def test_declared_oversize_is_rejected_before_reading_the_body() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/big").respond(200, content=b"x" * 2000)
        with pytest.raises(ContentTooLargeError):
            await make_fetcher(max_bytes=1000).fetch("http://example.edu/big")


async def test_streaming_body_without_content_length_is_cut_at_the_limit() -> None:
    async def stream() -> AsyncIterator[bytes]:
        for _ in range(100):
            yield b"x" * 100

    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/stream").mock(
            return_value=httpx.Response(200, content=stream())
        )
        with pytest.raises(ContentTooLargeError):
            await make_fetcher(max_bytes=1000).fetch("http://example.edu/stream")


async def test_gzip_bomb_is_limited_by_decompressed_size() -> None:
    bomb = gzip.compress(b"\0" * 5_000_000)  # tiny on the wire, 5 MB once decoded
    assert len(bomb) < 10_000

    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/bomb").respond(
            200, content=bomb, headers={"Content-Encoding": "gzip"}
        )
        with pytest.raises(ContentTooLargeError):
            await make_fetcher(max_bytes=100_000).fetch("http://example.edu/bomb")


async def test_body_exactly_at_the_limit_is_accepted() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/ok").respond(200, content=b"x" * 1000)
        result = await make_fetcher(max_bytes=1000).fetch("http://example.edu/ok")
    assert len(result.body) == 1000


async def test_total_time_budget_applies_even_when_each_step_is_slow() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, text="late")

    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/slow").mock(side_effect=slow)
        with pytest.raises(FetchTimeoutError):
            await make_fetcher(timeout_s=0.1).fetch("http://example.edu/slow")


async def test_httpx_timeout_is_mapped() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(FetchTimeoutError):
            await make_fetcher().fetch("http://example.edu/")


async def test_falls_back_to_next_address_on_connect_error() -> None:
    fetcher = make_fetcher({"example.edu": [PUBLIC_IP, OTHER_PUBLIC_IP]})
    with respx.mock(assert_all_called=True) as router:
        router.get(f"http://{PUBLIC_IP}/").mock(side_effect=httpx.ConnectError("refused"))
        router.get(f"http://{OTHER_PUBLIC_IP}/").respond(200, text="second")
        result = await fetcher.fetch("http://example.edu/")
    assert result.body == b"second"


async def test_all_addresses_failing_is_a_fetch_failure() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(FetchFailedError):
            await make_fetcher().fetch("http://example.edu/")


async def test_other_transport_errors_are_mapped() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"http://{PUBLIC_IP}/").mock(side_effect=httpx.RemoteProtocolError("bad"))
        with pytest.raises(FetchFailedError):
            await make_fetcher().fetch("http://example.edu/")


async def test_unresolvable_host_propagates() -> None:
    with pytest.raises(HostNotFoundError):
        await make_fetcher({}).fetch("http://nowhere.example/")


async def test_internal_start_url_never_hits_the_network() -> None:
    with respx.mock(assert_all_called=False) as router:
        anything = router.route().respond(200, text="SECRET")
        with pytest.raises(BlockedURLError):
            await make_fetcher().fetch("http://127.0.0.1/admin")
    assert not anything.called


async def test_client_ignores_proxy_env_and_never_follows_redirects_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # respx replaces the transport, so proxy handling can't be observed through behaviour;
    # check the client configuration directly. A proxy would receive the request instead of
    # the IP we validated, and httpx's own redirect handling would skip our per-hop checks.
    captured: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def spy(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    with respx.mock(assert_all_called=True) as router:
        router.get(f"http://{PUBLIC_IP}/").respond(200, text="ok")
        await make_fetcher().fetch("http://example.edu/")

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
