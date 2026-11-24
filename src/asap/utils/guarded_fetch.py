"""Guarded fetch for URLs that an untrusted party may influence (v2.6.0).

Used for JWKS, OIDC discovery, token introspection, and A2A Agent Cards
(one set of SSRF guards for all). It lives in ``asap.utils`` because
``asap.auth`` and ``asap.adapters`` both call it, and ``asap.transport``
imports ``asap.auth``: a module inside either package would create an import
cycle.

Rules: HTTPS only, with no user info in the URL; every address of the host
must be public; redirects are never followed; the body is read with a byte
limit and a total deadline. :func:`guarded_post_form_json` sends a form POST
(RFC 7662 introspection) under the same rules; its form and credentials never
appear in a URL, an error, or a log.
``allow_private_hosts=True`` disables the address checks for
local development and tests; HTTPS stays required.

DNS rebinding: :func:`guarded_fetch_json` resolves the host once, checks every
address, and connects to the first checked address. TLS SNI and certificate
verification still use the original hostname, so a second DNS answer cannot
move the connection. It does not guarantee more: a public address can still be
the wrong server, and an injected ``transport`` decides itself where to connect.

The address check is stricter than ``asap.transport.webhook.validate_callback_url``
(it also refuses shared address space ``100.64.0.0/10``, multicast, IPv6
site-local ``fec0::/10``, and reserved prefixes such as NAT64). Existing
callers keep their own guard.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

import httpx

DEFAULT_GUARDED_FETCH_TIMEOUT_SECONDS = 10.0
DEFAULT_GUARDED_FETCH_MAX_BYTES = 1024 * 1024

GuardedFetchRefusal = Literal[
    "invalid_url",
    "scheme_not_https",
    "host_not_public",
    "dns_failed",
    "redirect_refused",
    "http_status",
    "content_encoding",
    "too_large",
    "timeout",
    "transport_error",
    "invalid_json",
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

    ``is_global`` follows the IANA special-purpose registries. Multicast and
    reserved addresses are refused too, and so is the deprecated IPv6
    site-local block ``fec0::/10`` (RFC 3879), which ``ipaddress`` counts as
    global but which some networks still route internally.

    Example:
        >>> is_public_ip_address("10.0.0.1")
        False
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
        return False
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
    if parsed.userinfo:
        # Credentials go in a header; the message must not echo them.
        redacted = str(parsed.copy_with(userinfo=b""))
        raise GuardedFetchError(redacted, "invalid_url", "the URL holds user info")
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
        # getaddrinfo IDNA-encodes the name first: an empty label or one over
        # 63 bytes raises UnicodeError, which httpx.URL does not catch.
        except (OSError, UnicodeError) as exc:
            raise GuardedFetchError(str(url), "dns_failed", f"{host!r}: {exc}") from exc
    if not addresses:
        raise GuardedFetchError(str(url), "dns_failed", f"{host!r} has no address")
    blocked = [a for a in addresses if not is_public_ip_address(a)]
    if blocked and not allow_private_hosts:
        raise GuardedFetchError(
            str(url), "host_not_public", f"{host!r} resolves to non-public {blocked}"
        )
    return addresses


def _refuse_json_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _check_response_head(url: str, response: httpx.Response, max_bytes: int) -> None:
    status = response.status_code
    if 300 <= status < 400:
        raise GuardedFetchError(url, "redirect_refused", f"status {status}; redirects are refused")
    if not response.is_success:
        raise GuardedFetchError(url, "http_status", f"status {status}, expected 2xx")
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding != "identity":
        raise GuardedFetchError(url, "content_encoding", f"{encoding!r}, expected 'identity'")
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise GuardedFetchError(url, "too_large", f"{declared} bytes declared, limit {max_bytes}")


async def _read_limited_body(url: str, response: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    # Identity encoding (checked above) means no decompression happens here, so
    # the limit counts the bytes that crossed the wire.
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise GuardedFetchError(url, "too_large", f"body exceeds {max_bytes} bytes")
    return bytes(body)


async def _fetch_pinned(
    url: httpx.URL,
    address: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
    form: Mapping[str, str] | None,
    basic_auth: tuple[str, str] | None,
) -> bytes:
    host = url.raw_host.decode("ascii")
    headers = {
        "Host": url.netloc.decode("ascii"),
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    extensions = {} if _is_ip_literal(host) else {"sni_hostname": host}
    async with (
        # trust_env=False: an HTTP(S)_PROXY variable would move the connection
        # to the proxy and void the address check.
        httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client,
        client.stream(
            "GET" if form is None else "POST",
            url.copy_with(host=address),
            headers=headers,
            extensions=extensions,
            data=form,
            auth=basic_auth,
        ) as response,
    ):
        _check_response_head(str(url), response, max_bytes)
        return await _read_limited_body(str(url), response, max_bytes)


async def _guarded_json_request(
    url: str,
    *,
    form: Mapping[str, str] | None,
    basic_auth: tuple[str, str] | None,
    allow_private_hosts: bool,
    max_bytes: int,
    timeout_seconds: float,
    resolver: AddressResolver | None,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    parsed = validate_guarded_url(url, allow_private_hosts=allow_private_hosts)
    try:
        async with asyncio.timeout(timeout_seconds):
            addresses = await resolve_public_addresses(
                parsed, allow_private_hosts=allow_private_hosts, resolver=resolver
            )
            body = await _fetch_pinned(
                parsed,
                addresses[0],
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
                transport=transport,
                form=form,
                basic_auth=basic_auth,
            )
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise GuardedFetchError(url, "timeout", f"no response within {timeout_seconds}s") from exc
    except httpx.HTTPError as exc:
        raise GuardedFetchError(url, "transport_error", type(exc).__name__) from exc
    try:
        document = json.loads(body, parse_constant=_refuse_json_constant)
    except ValueError as exc:
        raise GuardedFetchError(url, "invalid_json", str(exc)) from exc
    except RecursionError:
        # json.loads raises RecursionError, not ValueError, past about 10,000 levels.
        raise GuardedFetchError(url, "invalid_json", "the JSON is nested too deeply") from None
    if not isinstance(document, dict):
        raise GuardedFetchError(url, "invalid_json", f"top level is {type(document).__name__}")
    return document


async def guarded_fetch_json(
    url: str,
    *,
    allow_private_hosts: bool = False,
    max_bytes: int = DEFAULT_GUARDED_FETCH_MAX_BYTES,
    timeout_seconds: float = DEFAULT_GUARDED_FETCH_TIMEOUT_SECONDS,
    resolver: AddressResolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """GET a JSON object through the guard.

    ``timeout_seconds`` bounds the whole fetch, DNS included. Raises
    :class:`GuardedFetchError` for every refusal or failure.

    Example:
        >>> jwks = await guarded_fetch_json("https://issuer.example/jwks.json")
    """
    return await _guarded_json_request(
        url,
        form=None,
        basic_auth=None,
        allow_private_hosts=allow_private_hosts,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        resolver=resolver,
        transport=transport,
    )


async def guarded_post_form_json(
    url: str,
    form: Mapping[str, str],
    *,
    basic_auth: tuple[str, str] | None = None,
    allow_private_hosts: bool = False,
    max_bytes: int = DEFAULT_GUARDED_FETCH_MAX_BYTES,
    timeout_seconds: float = DEFAULT_GUARDED_FETCH_TIMEOUT_SECONDS,
    resolver: AddressResolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST ``form`` (``application/x-www-form-urlencoded``) through the guard; read a JSON object.

    Same rules as :func:`guarded_fetch_json`. ``basic_auth`` is sent as HTTP
    Basic client authentication (RFC 6749 §2.3.1). Errors name the URL and the
    reason only, never the form or the credentials.

    Example:
        >>> info = await guarded_post_form_json(url, {"token": token}, basic_auth=(cid, secret))
    """
    return await _guarded_json_request(
        url,
        form=form,
        basic_auth=basic_auth,
        allow_private_hosts=allow_private_hosts,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        resolver=resolver,
        transport=transport,
    )
