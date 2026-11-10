"""Unit tests for registration-specific rate limit helpers."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from starlette.datastructures import Headers

from asap.transport.rate_limit import (
    create_registration_rate_limiter,
    registration_token_key,
)


def test_registration_token_key_stable_per_bearer_secret() -> None:
    req = MagicMock()
    req.headers = Headers({"authorization": "Bearer same-secret"})
    req.client = MagicMock()
    req.client.host = "203.0.113.10"
    k1 = registration_token_key(req)
    k2 = registration_token_key(req)
    assert k1 == k2
    assert k1.startswith("regtok:")


def test_registration_token_key_fallback_uses_client_ip() -> None:
    req = MagicMock()
    req.headers = Headers({})
    req.client = MagicMock()
    req.client.host = "198.51.100.2"
    key = registration_token_key(req)
    assert key == "ip:198.51.100.2"


def test_registration_token_key_empty_bearer_falls_back_to_ip() -> None:
    req = MagicMock()
    req.headers = Headers({"authorization": "Bearer "})
    req.client = MagicMock()
    req.client.host = "198.51.100.3"
    key = registration_token_key(req)
    assert key.startswith("ip:")


def _registration_key(authorization: str | None, host: str) -> str:
    """Build a registration limiter key for one Authorization header and client host."""
    req = MagicMock()
    if authorization is None:
        req.headers = Headers({})
    else:
        req.headers = Headers({"authorization": authorization})
    req.client = MagicMock()
    req.client.host = host
    return registration_token_key(req)


def test_registration_token_key_hashes_trimmed_bearer_and_ignores_blank_padding() -> None:
    """Padding must not mint a new 5/hour bucket or escape the IP fallback.

    Auto-registration limits ``POST /registry/agents`` by the SHA-256 of the
    Bearer secret. Spaces around that secret, or a whitespace-only Bearer
    value, have to collapse onto the trimmed secret or the client IP.
    """
    host = "198.51.100.8"
    expected = "regtok:" + hashlib.sha256(b"secret-token").hexdigest()

    assert _registration_key("Bearer secret-token", host) == expected
    assert _registration_key("Bearer   secret-token  ", host) == expected
    assert _registration_key("Bearer \t secret-token \n", host) == expected
    assert _registration_key("Bearer   ", host) == f"ip:{host}"
    assert _registration_key("Bearer \t ", host) == f"ip:{host}"
    assert _registration_key(None, host) == f"ip:{host}"
    assert _registration_key("Bearer other-token", host) != expected


def test_create_registration_rate_limiter_has_hour_budget() -> None:
    limiter = create_registration_rate_limiter()
    assert len(limiter.limits) >= 1
