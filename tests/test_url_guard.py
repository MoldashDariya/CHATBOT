import pytest

from bot.errors import BlockedURLError, HostNotFoundError, InvalidURLError
from bot.security.url_guard import (
    GuardedTarget,
    Resolver,
    guard_url,
    is_public_ip,
    parse_url,
    system_resolve,
)

PUBLIC_IP = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        try:
            return mapping[host]
        except KeyError:
            raise HostNotFoundError(host) from None

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata endpoint
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind
        "100.64.0.1",  # carrier-grade NAT
        "224.0.0.1",  # multicast
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%en0",
        "fc00::1",
        "fd12:3456::1",
        "ff02::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 wrapping 169.254.169.254
        "2002:7f00:1::1",  # 6to4 wrapping 127.0.0.1
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    ],
)
def test_non_public_addresses_rejected(address: str) -> None:
    assert not is_public_ip(address)


@pytest.mark.parametrize(
    "address",
    [
        PUBLIC_IP,
        "8.8.8.8",
        "1.1.1.1",
        "2606:4700:4700::1111",
        # Wrapped formats are judged by the IPv4 address inside, in both directions:
        "::ffff:93.184.216.34",  # IPv4-mapped public address
        "2002:5db8:d822::1",  # 6to4 wrapping 93.184.216.34
        "64:ff9b::5db8:d822",  # NAT64 wrapping 93.184.216.34
    ],
)
def test_public_addresses_accepted(address: str) -> None:
    assert is_public_ip(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:80/admin",
        "https://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:7f00:1]/",
        "http://10.1.2.3/",
        "http://192.168.0.1/",
        "http://0.0.0.0/",
        "http://[fe80::1]/",
    ],
)
async def test_literal_internal_addresses_blocked(url: str) -> None:
    with pytest.raises(BlockedURLError):
        await guard_url(url, resolver_for({}))


@pytest.mark.parametrize("host", ["localhost", "localhost.", "metadata.internal", "intranet"])
async def test_hostnames_resolving_to_internal_addresses_blocked(host: str) -> None:
    resolver = resolver_for({host: ["127.0.0.1"]})
    with pytest.raises(BlockedURLError):
        await guard_url(f"http://{host}/", resolver)


async def test_one_internal_address_among_public_ones_blocks_the_host() -> None:
    # An attacker-controlled DNS name can return a mix; connecting to any of them is unsafe.
    resolver = resolver_for({"evil.example": [PUBLIC_IP, "10.0.0.5"]})
    with pytest.raises(BlockedURLError):
        await guard_url("https://evil.example/", resolver)


async def test_ipv6_mapped_loopback_from_dns_blocked() -> None:
    resolver = resolver_for({"evil.example": ["::ffff:127.0.0.1"]})
    with pytest.raises(BlockedURLError):
        await guard_url("https://evil.example/", resolver)


@pytest.mark.parametrize(
    "url", ["http://2130706433/", "http://127.1/", "http://0x7f.1/", "http://017700000001/"]
)
async def test_alternative_loopback_notations_never_pass(url: str) -> None:
    # These go through the real system resolver, which expands them to 127.0.0.1; on a
    # platform that refuses to parse them the lookup fails instead. Either way: no fetch.
    with pytest.raises((BlockedURLError, HostNotFoundError)):
        await guard_url(url, system_resolve)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.edu/file",
        "file:///etc/passwd",
        "gopher://example.edu/",
        "javascript:alert(1)",
        "example.edu/no-scheme",
        "http://",
        "http:///path",
        "",
        "http://exa mple.edu/",
        "http://example.edu/\r\nHost: evil",
        "http://user:pass@example.edu/",
        "http://example.edu:abc/",
        "http://example.edu:99999/",
        "https://" + "a" * 2100 + ".edu/",
    ],
)
async def test_malformed_or_unsupported_urls_rejected(url: str) -> None:
    with pytest.raises(InvalidURLError):
        await guard_url(url, resolver_for({"example.edu": [PUBLIC_IP]}))


@pytest.mark.parametrize(
    "url", ["http://example.edu:22/", "https://example.edu:6379/", "http://example.edu:8080/"]
)
async def test_non_web_ports_blocked(url: str) -> None:
    with pytest.raises(BlockedURLError):
        await guard_url(url, resolver_for({"example.edu": [PUBLIC_IP]}))


async def test_userinfo_trick_cannot_hide_an_internal_host() -> None:
    # Browsers/parsers disagree about which host this is; we refuse the form outright.
    with pytest.raises(InvalidURLError):
        await guard_url("http://example.edu@127.0.0.1/", resolver_for({"example.edu": [PUBLIC_IP]}))


async def test_unresolvable_host() -> None:
    with pytest.raises(HostNotFoundError):
        await guard_url("https://no-such-host.example/", resolver_for({}))


async def test_empty_resolution_result() -> None:
    with pytest.raises(HostNotFoundError):
        await guard_url("https://example.edu/", resolver_for({"example.edu": []}))


async def test_public_host_passes_and_pins_validated_addresses() -> None:
    target = await guard_url(
        "https://Example.EDU/admissions?year=2027#apply", resolver_for({"example.edu": [PUBLIC_IP]})
    )

    assert target == GuardedTarget(
        url="https://Example.EDU/admissions?year=2027#apply",
        scheme="https",
        host="example.edu",
        port=443,
        request_target="/admissions?year=2027",
        addresses=(PUBLIC_IP,),
    )
    assert target.pinned_url(PUBLIC_IP) == f"https://{PUBLIC_IP}/admissions?year=2027"
    assert target.host_header == "example.edu"


async def test_resolver_is_called_with_the_effective_port() -> None:
    seen: list[tuple[str, int]] = []

    async def resolve(host: str, port: int) -> list[str]:
        seen.append((host, port))
        return [PUBLIC_IP]

    await guard_url("http://example.edu/", resolve)
    await guard_url("https://example.edu/", resolve)
    assert seen == [("example.edu", 80), ("example.edu", 443)]


async def test_public_ipv6_literal_is_bracketed_when_pinned() -> None:
    target = await guard_url("http://[2606:4700:4700::1111]:80/x", resolver_for({}))
    assert target.pinned_url("2606:4700:4700::1111") == "http://[2606:4700:4700::1111]/x"


async def test_idn_host_is_sent_as_punycode() -> None:
    target = await guard_url(
        "https://университет.рф/", resolver_for({"xn--b1agajc0ayikbb.xn--p1ai": [PUBLIC_IP]})
    )
    assert target.host == "xn--b1agajc0ayikbb.xn--p1ai"


def test_parse_url_defaults_path() -> None:
    assert parse_url("http://example.edu") == ("http", "example.edu", 80, "/")
