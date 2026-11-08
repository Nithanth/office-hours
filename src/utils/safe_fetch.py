"""SSRF-safe outbound HTTP for client-supplied URLs.

Use :func:`safe_session` + :func:`open_get` / :func:`safe_get` whenever the
server fetches a URL a client controls (paper links, proxied docs, scraped
PDF hrefs). Two layers enforce that only public addresses are reached:

* Every URL, including each redirect hop, goes through :func:`check_url`
  (http(s) only, no internal hostnames, no non-public IP literals).
  Redirects are followed by hand so no hop skips the check.
* The session's connector resolves names through :class:`PublicOnlyResolver`,
  which refuses any answer containing a non-public address. aiohttp connects
  to exactly the addresses the resolver vetted, so a second DNS lookup can't
  swap in an internal target (DNS rebinding).

Bodies are read with a hard byte cap via :func:`read_capped`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

MAX_REDIRECTS = 5
DEFAULT_MAX_HTML_BYTES = 10 * 1024 * 1024
_CHUNK = 64 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})
_BLOCKED_HOST_SUFFIXES = (".internal", ".local", ".localhost")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeURLError(ValueError):
    """The URL (or an address it resolves/redirects to) is not public."""


class ResponseTooLargeError(ValueError):
    """The response body exceeded the caller's byte cap."""


def is_public_ip(ip: IPAddress) -> bool:
    """True only for globally routable unicast addresses."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip.teredo:
            embedded = ip.teredo[1]
        if embedded is not None and not is_public_ip(embedded):
            return False
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> str:
    """Validate *url* syntactically; return its normalised host.

    Raises :class:`UnsafeURLError` for non-http(s) schemes, missing hosts,
    internal hostnames and non-public IP literals. Hostnames are resolved and
    vetted later, at connect time, by :class:`PublicOnlyResolver`.
    """
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError as exc:
        raise UnsafeURLError(f"malformed URL: {url!r}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme not allowed: {parts.scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError(f"internal host: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if not is_public_ip(ip):
        raise UnsafeURLError(f"non-public address: {host}")
    return host


def is_safe_url(url: str) -> bool:
    """Boolean form of :func:`check_url` (no DNS resolution)."""
    try:
        check_url(url)
    except UnsafeURLError:
        return False
    return True


class PublicOnlyResolver(AbstractResolver):
    """Resolver that refuses hostnames resolving to any non-public address."""

    def __init__(self) -> None:
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results: list[ResolveResult] = await self._inner.resolve(host, port, family)
        for result in results:
            try:
                ip = ipaddress.ip_address(result["host"])
            except ValueError as exc:
                raise UnsafeURLError(f"unparseable address for {host}") from exc
            if not is_public_ip(ip):
                raise UnsafeURLError(f"{host} resolves to non-public address {ip}")
        return results

    async def close(self) -> None:
        await self._inner.close()


def safe_session(**kwargs: Any) -> aiohttp.ClientSession:
    """A ClientSession whose connections can only reach public addresses.

    Fetch through :func:`open_get` / :func:`safe_get`; a bare ``session.get``
    would follow redirects (and IP-literal hops) without re-checking them.
    """
    connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
    return aiohttp.ClientSession(connector=connector, trust_env=False, **kwargs)


async def open_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> aiohttp.ClientResponse:
    """GET *url*, following redirects by hand and re-checking every hop.

    The caller owns the returned response and must ``release()`` it.
    """
    current = url
    for _ in range(max_redirects + 1):
        check_url(current)
        kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = await session.get(current, **kwargs)
        location = resp.headers.get("Location")
        if resp.status not in _REDIRECT_STATUSES or not location:
            return resp
        resp.release()
        current = urljoin(str(resp.url), location)
    raise UnsafeURLError(f"too many redirects fetching {url}")


@asynccontextmanager
async def safe_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Context-manager form of :func:`open_get`."""
    resp = await open_get(
        session, url, headers=headers, timeout=timeout, max_redirects=max_redirects
    )
    try:
        yield resp
    finally:
        resp.release()


async def read_capped(resp: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    """Read the body, raising :class:`ResponseTooLargeError` past *max_bytes*."""
    declared = resp.content_length
    if declared is not None and declared > max_bytes:
        raise ResponseTooLargeError(f"declared {declared} bytes > {max_bytes}")
    buf = bytearray()
    async for chunk in resp.content.iter_chunked(_CHUNK):
        buf += chunk
        if len(buf) > max_bytes:
            raise ResponseTooLargeError(f"body exceeded {max_bytes} bytes")
    return bytes(buf)


async def text_capped(
    resp: aiohttp.ClientResponse, max_bytes: int = DEFAULT_MAX_HTML_BYTES
) -> str:
    """Decode the body as text with a byte cap (charset from headers, else UTF-8)."""
    body = await read_capped(resp, max_bytes)
    try:
        return body.decode(resp.charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
