"""Server-side request forgery (SSRF) guard: tools that open URLs must not reach internal networks or cloud metadata.

Set ALLOW_PRIVATE_HOSTS=true for local development (e.g. a Postgres container on the Docker network).
Redirects and DNS rebinding are covered by validating at CONNECT time: `guarded_client()` resolves the name itself, checks
every address, and connects to the checked address, so a redirect hop or a second DNS answer can't reach an internal host.
Databases get the same treatment through `pin_host()`. Still restrict the backend's network egress in production (defence in depth).
"""
import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse


class BlockedHost(ValueError):
    """The destination is an internal or otherwise disallowed address."""


def allow_private() -> bool:
    return os.getenv("ALLOW_PRIVATE_HOSTS", "").strip().lower() in ("1", "true", "yes")


def _bad(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified or ip.is_global is False


def resolve_public(host: str | None) -> list[str]:
    """Resolve `host` and return its addresses, refusing the lot if any is internal."""
    if not host:
        raise BlockedHost("No host given.")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        infos = [str(literal)]
    except ValueError:
        try:
            infos = list(dict.fromkeys(i[4][0].split("%")[0] for i in socket.getaddrinfo(host, None)))
        except socket.gaierror as e:
            raise BlockedHost(f"Couldn't resolve '{host}'.") from e
    if not allow_private():
        for addr in infos:
            if _bad(ipaddress.ip_address(addr)):
                raise BlockedHost(f"'{host}' points to an internal address, which isn't allowed.")
    return infos


def check_host(host: str | None) -> None:
    resolve_public(host)


def pin_host(host: str | None) -> str:
    """The checked address to connect to (so the driver can't re-resolve the name to something else)."""
    return resolve_public(host)[0]


def check_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise BlockedHost("Only http and https links are allowed.")
    check_host(parsed.hostname)


# ---------------- guarded HTTP client ----------------


def guarded_client(**kwargs):
    """An httpx client that validates every connection's address (initial request and each redirect hop)."""
    import httpcore
    import httpx
    from httpcore._backends.anyio import AnyIOBackend

    class Backend(AnyIOBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            addrs = await asyncio.get_running_loop().run_in_executor(None, resolve_public, host)
            return await super().connect_tcp(addrs[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options)

    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(network_backend=Backend(), max_connections=10)
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("max_redirects", 5)
    kwargs.setdefault("timeout", 15)
    return httpx.AsyncClient(transport=transport, trust_env=False, **kwargs)
