"""Guarded fetch for URLs that an untrusted party may influence (v2.6.0).

Used for JWKS, OIDC discovery, token introspection, and A2A Agent Cards
(one set of SSRF guards for all). It lives in ``asap.utils`` because
``asap.auth`` and ``asap.adapters`` both call it, and ``asap.transport``
imports ``asap.auth``: a module inside either package would create an import
cycle.

This part holds the address checks: HTTPS only, and every address of the host
must be public. ``allow_private_hosts=True`` disables the address checks for
local development and tests; HTTPS stays required.

The address check is stricter than ``asap.transport.webhook.validate_callback_url``
(it also refuses shared address space ``100.64.0.0/10``, multicast, and
reserved prefixes such as NAT64). Existing callers keep their own guard.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx

GuardedFetchRefusal = Literal[
    "invalid_url",
    "scheme_not_https",
    "host_not_public",
    "dns_failed",
]
AddressResolver = Callable[[str], Awaitable[list[str]]]


class GuardedFetchError(Exception):
    """A guarded fetch was refused or failed; ``code`` is stable, the message is not.

    The message holds the URL and the reason, never a response body or a credential.
    """

    def __init__(self, url: str, code: GuardedFetchRefusal, detail: str) -> None:
        super().__init__(f"guarded fetch of {url!r} refused ({code}): {detail}")
        self.url = url
        self.code: GuardedFetchRefusal = code


def is_public_ip_address(address: str) -> bool:
    """Return True if ``address`` is a globally routable unicast address.

    Example:
        >>> is_public_ip_address("10.0.0.1")
        False
    """
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _is_localhost_name(host: str) -> bool:
    # RFC 6761 §6.3: "localhost" and its subdomains always mean loopback.
    name = host.rstrip(".")
    return name == "localhost" or name.endswith(".localhost")


def validate_guarded_url(url: str, *, allow_private_hosts: bool = False) -> httpx.URL:
    """Check a URL without DNS: HTTPS, a host, and no non-public IP literal or localhost name.

    Example:
        >>> validate_guarded_url("https://issuer.example/jwks").host
        'issuer.example'
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise GuardedFetchError(url, "invalid_url", str(exc)) from exc
    if parsed.scheme != "https":
        raise GuardedFetchError(
            url, "scheme_not_https", f"scheme is {parsed.scheme!r}, expected 'https'"
        )
    host = parsed.raw_host.decode("ascii")
    if not host:
        raise GuardedFetchError(url, "invalid_url", "the URL has no host")
    if allow_private_hosts:
        return parsed
    if _is_localhost_name(host) or (_is_ip_literal(host) and not is_public_ip_address(host)):
        raise GuardedFetchError(url, "host_not_public", f"host {host!r} is not a public address")
    return parsed


async def _resolve_with_getaddrinfo(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def resolve_public_addresses(
    url: httpx.URL,
    *,
    allow_private_hosts: bool = False,
    resolver: AddressResolver | None = None,
) -> list[str]:
    """Resolve the URL host and refuse it if any address is not public.

    Connect only to a returned address: resolving the name again before the
    connection reopens the DNS rebinding window.

    Example:
        >>> addresses = await resolve_public_addresses(validate_guarded_url(jwks_uri))
    """
    host = url.raw_host.decode("ascii")
    if _is_ip_literal(host):
        addresses = [host]
    else:
        try:
            addresses = await (resolver or _resolve_with_getaddrinfo)(host)
        except OSError as exc:
            raise GuardedFetchError(str(url), "dns_failed", f"{host!r}: {exc}") from exc
    if not addresses:
        raise GuardedFetchError(str(url), "dns_failed", f"{host!r} has no address")
    blocked = [a for a in addresses if not is_public_ip_address(a)]
    if blocked and not allow_private_hosts:
        raise GuardedFetchError(
            str(url), "host_not_public", f"{host!r} resolves to non-public {blocked}"
        )
    return addresses
