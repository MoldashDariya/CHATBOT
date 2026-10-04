"""SSRF protection: decide whether a URL may be fetched and which IPs it may be fetched from.

The caller must connect to one of `GuardedTarget.addresses` (not re-resolve the hostname),
otherwise a DNS-rebinding server can answer "public" for our check and "internal" for the
real connection.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from bot.errors import BlockedURLError, HostNotFoundError, InvalidURLError

DEFAULT_PORTS = {"http": 80, "https": 443}
ALLOWED_PORTS = frozenset(DEFAULT_PORTS.values())
MAX_URL_LENGTH = 2048

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_TEREDO = ipaddress.ip_network("2001::/32")

# (host, port) -> IP address strings. Injectable so tests need no network.
Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class GuardedTarget:
    url: str
    scheme: str
    host: str  # ASCII (punycode) hostname: what goes into Host header and TLS SNI
    port: int
    request_target: str  # path + query, always starts with "/"
    addresses: tuple[str, ...]  # validated public IPs, safe to connect to

    @property
    def host_header(self) -> str:
        return self.host if self.port == DEFAULT_PORTS[self.scheme] else f"{self.host}:{self.port}"

    def pinned_url(self, address: str) -> str:
        """The same request, addressed to an already-validated IP instead of the hostname."""
        ip = f"[{address}]" if ":" in address else address
        netloc = ip if self.port == DEFAULT_PORTS[self.scheme] else f"{ip}:{self.port}"
        return f"{self.scheme}://{netloc}{self.request_target}"


def is_public_ip(address: str) -> bool:
    """True only for globally routable unicast addresses."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address):
        # Check the IPv4 address hidden inside tunnelling/mapping formats instead of the wrapper.
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip in _TEREDO:
            return False
    # is_global alone is not enough: it reports multicast ranges as global.
    return ip.is_global and not ip.is_multicast


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise HostNotFoundError(host) from exc
    # dict.fromkeys: unique addresses, resolver order preserved.
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def parse_url(url: str) -> tuple[str, str, int, str]:
    """Syntactic validation. Returns (scheme, ascii_host, port, request_target)."""
    if len(url) > MAX_URL_LENGTH or any(c.isspace() or ord(c) < 32 for c in url):
        raise InvalidURLError("malformed URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError("malformed URL") from exc

    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidURLError("only http and https are supported")
    if parts.username is not None or parts.password is not None:
        raise InvalidURLError("credentials in URL are not allowed")
    host = parts.hostname
    if not host:
        raise InvalidURLError("missing host")
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError as exc:
        raise InvalidURLError("invalid host name") from exc

    port = port if port is not None else DEFAULT_PORTS[scheme]
    if port not in ALLOWED_PORTS:
        raise BlockedURLError(f"port {port} is not allowed")

    request_target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return scheme, ascii_host, port, request_target


async def guard_url(url: str, resolve: Resolver = system_resolve) -> GuardedTarget:
    """Validate `url` and resolve it. Every resolved address must be public, not just one."""
    scheme, host, port, request_target = parse_url(url)

    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        # Not a literal. Exotic literal forms ("127.1", "2130706433", "0x7f.1") end up here
        # too: the system resolver expands them and the result is checked like any other.
        addresses = await resolve(host, port)

    if not addresses:
        raise HostNotFoundError(host)
    if not all(is_public_ip(address) for address in addresses):
        raise BlockedURLError("host resolves to a non-public address")

    return GuardedTarget(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        request_target=request_target,
        addresses=tuple(addresses),
    )
