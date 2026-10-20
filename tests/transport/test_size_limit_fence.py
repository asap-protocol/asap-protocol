"""Early Content-Length rejection uses an exclusive maximum.

``SizeLimitMiddleware`` forwards a request whose Content-Length equals
``max_size`` and returns 413 only when the header is greater. An inclusive
comparison would refuse the largest request the configuration allows.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from asap.transport.middleware import SizeLimitMiddleware

_CAP = 10


def _request(content_length: str) -> Request:
    """Return a request double whose only inspected field is Content-Length."""
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/asap",
            "raw_path": b"/asap",
            "query_string": b"",
            "headers": [(b"content-length", content_length.encode("ascii"))],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
    )


async def _ok(_request: Request) -> JSONResponse:
    """Stand in for the next middleware."""
    return JSONResponse(content={"status": "ok"})


@pytest.mark.asyncio
async def test_content_length_equal_to_cap_is_forwarded() -> None:
    """Content-Length equal to max_size reaches the rest of the stack."""
    middleware = SizeLimitMiddleware(FastAPI(), max_size=_CAP)
    seen = False

    async def _call_next(request: Request) -> JSONResponse:
        nonlocal seen
        seen = True
        return await _ok(request)

    response = await middleware.dispatch(_request(str(_CAP)), _call_next)

    assert seen is True
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_content_length_one_byte_over_cap_is_rejected() -> None:
    """Content-Length of max_size + 1 is rejected before the next handler."""
    middleware = SizeLimitMiddleware(FastAPI(), max_size=_CAP)
    seen = False

    async def _call_next(request: Request) -> JSONResponse:
        nonlocal seen
        seen = True
        return await _ok(request)

    response = await middleware.dispatch(_request(str(_CAP + 1)), _call_next)

    assert seen is False
    assert response.status_code == 413
    detail = json.loads(bytes(response.body))
    assert detail["detail"] == f"Request size ({_CAP + 1} bytes) exceeds maximum ({_CAP} bytes)"
