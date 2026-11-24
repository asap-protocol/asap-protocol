"""One bounded HTTP exchange for the compliance transports.

Each request has one deadline for the whole answer and a body size cap, and it
never follows a redirect: a 3xx comes back as it is. An error names the error
type only, because an httpx error text can quote a request header, and the
``Authorization`` header holds a probe token.

Example::

    answer = await bounded_http_exchange(
        "GET", url, headers={}, timeout_seconds=10.0, max_bytes=1_048_576,
        http_transport=None, where=f"GET {url}",
    )
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx


class HttpExchangeError(RuntimeError):
    """A request got no complete HTTP answer: network error, deadline, or size cap."""


@dataclass(frozen=True)
class RawHttpAnswer:
    """The status, headers, and body bytes of one complete HTTP answer."""

    status_code: int
    headers: httpx.Headers
    body: bytes


async def bounded_http_exchange(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
    http_transport: httpx.AsyncBaseTransport | None,
    where: str,
    json_body: Mapping[str, Any] | None = None,
    error_type: type[HttpExchangeError] = HttpExchangeError,
) -> RawHttpAnswer:
    """Send one request; raise *error_type* (text: *where* and the reason) without a full answer."""
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _exchange(
                method, url, headers, json_body, timeout_seconds, max_bytes, http_transport
            )
    except TimeoutError as exc:
        raise error_type(f"{where} got no full answer within {timeout_seconds}s") from exc
    except _BodyTooLarge:
        raise error_type(f"response body is over {max_bytes} bytes") from None
    except httpx.HTTPError as exc:
        # Type only, and no chained cause: an httpx error text can quote a request header.
        raise error_type(f"{where} failed: {type(exc).__name__}") from None


class _BodyTooLarge(Exception):
    pass


async def _exchange(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: Mapping[str, Any] | None,
    timeout_seconds: float,
    max_bytes: int,
    http_transport: httpx.AsyncBaseTransport | None,
) -> RawHttpAnswer:
    async with (
        httpx.AsyncClient(
            transport=http_transport, timeout=timeout_seconds, follow_redirects=False
        ) as client,
        client.stream(method, url, headers=dict(headers), json=json_body) as response,
    ):
        received = bytearray()
        async for chunk in response.aiter_bytes():
            received.extend(chunk)
            if len(received) > max_bytes:
                raise _BodyTooLarge
        return RawHttpAnswer(response.status_code, response.headers, bytes(received))
