"""Tests for shared transport authentication helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from starlette.requests import Request

from asap.auth.agent_jwt import (
    HOST_REVOKED_ERROR,
    JtiReplayCache,
    JwtVerifyResult,
    create_host_jwt,
)
from asap.auth.identity import HostIdentity, InMemoryHostStore
from asap.transport._auth_helpers import bearer_token_from_request, verify_host_bearer
from tests.crypto.jwk_helpers import ed25519_public_jwk

_HOST_JWT_AUDIENCE = "urn:asap:agent:test-auth-helper"


def _request_with_authorization(value: str | None) -> Request:
    """Build a minimal HTTP request carrying an optional Authorization header."""
    headers: list[tuple[bytes, bytes]] = []
    if value is not None:
        headers.append((b"authorization", value.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
        }
    )


def test_bearer_token_from_request_extracts_token() -> None:
    request = _request_with_authorization("Bearer abc.def")

    assert bearer_token_from_request(request) == "abc.def"


def test_bearer_token_from_request_strips_token_whitespace() -> None:
    request = _request_with_authorization("Bearer   abc.def   ")

    assert bearer_token_from_request(request) == "abc.def"


def test_bearer_token_from_request_missing_or_empty_returns_none() -> None:
    assert bearer_token_from_request(_request_with_authorization(None)) is None
    assert bearer_token_from_request(_request_with_authorization("Bearer ")) is None
    assert bearer_token_from_request(_request_with_authorization("Bearer   ")) is None


def test_bearer_token_from_request_non_bearer_returns_none() -> None:
    request = _request_with_authorization("Basic abc.def")

    assert bearer_token_from_request(request) is None


def _http_request(app: FastAPI, *, authorization: str | None) -> Request:
    """Build a minimal ASGI HTTP request bound to *app*."""
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def _identity_app(host_store: InMemoryHostStore) -> FastAPI:
    app = FastAPI()
    app.state.identity_host_store = host_store
    app.state.identity_jwt_audience = _HOST_JWT_AUDIENCE
    return app


async def _seed_active_host(host_sk: Ed25519PrivateKey, host_store: InMemoryHostStore) -> None:
    now = datetime.now(timezone.utc)
    pub = ed25519_public_jwk(host_sk)
    await host_store.save(
        HostIdentity(
            host_id="host-active",
            public_key=pub,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.asyncio
async def test_verify_host_bearer_missing_token_returns_401() -> None:
    """Unauthenticated requests must receive 401 with WWW-Authenticate."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization=None)

    result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 401
    assert err.body == b'{"detail":"Authentication required"}'
    assert err.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_bearer_valid_token_returns_result() -> None:
    """A valid Host JWT for an active host returns JwtVerifyResult."""
    host_sk = Ed25519PrivateKey.generate()
    host_store = InMemoryHostStore()
    await _seed_active_host(host_sk, host_store)
    app = _identity_app(host_store)
    token = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
    request = _http_request(app, authorization=f"Bearer {token}")

    result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert err is None
    assert result is not None
    assert result.ok is True
    assert isinstance(result.claims, dict)
    assert result.claims.get("iss")


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_bearer_read_only_jti_check_preserves_polling() -> None:
    """Read-only JTI checks allow polling but reject tokens spent elsewhere."""
    host_sk = Ed25519PrivateKey.generate()
    host_store = InMemoryHostStore()
    await _seed_active_host(host_sk, host_store)
    app = _identity_app(host_store)
    token = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
    cache = JtiReplayCache()

    for _ in range(2):
        result, err = await verify_host_bearer(
            _http_request(app, authorization=f"Bearer {token}"),
            jti_replay_cache=cache,
            record_jti=False,
        )
        assert err is None
        assert result is not None
        assert result.ok is True

    recorded_result, recorded_err = await verify_host_bearer(
        _http_request(app, authorization=f"Bearer {token}"),
        jti_replay_cache=cache,
    )
    assert recorded_err is None
    assert recorded_result is not None
    assert recorded_result.ok is True

    replay_result, replay_err = await verify_host_bearer(
        _http_request(app, authorization=f"Bearer {token}"),
        jti_replay_cache=cache,
        record_jti=False,
    )
    assert replay_result is None
    assert replay_err is not None
    assert replay_err.status_code == 401
    assert replay_err.body == b'{"detail":"jti replay detected"}'


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_bearer_revoked_host_returns_403() -> None:
    """Revoked hosts must map HOST_REVOKED_ERROR to HTTP 403 (CR#7)."""
    host_sk = Ed25519PrivateKey.generate()
    host_store = InMemoryHostStore()
    now = datetime.now(timezone.utc)
    pub = ed25519_public_jwk(host_sk)
    await host_store.save(
        HostIdentity(
            host_id="host-revoked",
            public_key=pub,
            status="revoked",
            created_at=now,
            updated_at=now,
        )
    )
    app = _identity_app(host_store)
    token = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
    request = _http_request(app, authorization=f"Bearer {token}")

    result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 403
    assert err.body == b'{"detail":"host revoked"}'


@pytest.mark.asyncio
async def test_verify_host_bearer_invalid_token_returns_401() -> None:
    """Signature or claim failures surface as 401."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer not-a-jwt")

    result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 401


@pytest.mark.asyncio
async def test_verify_host_bearer_missing_iss_returns_400() -> None:
    """Tokens that verify but omit iss must be rejected with 400."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer synthetic")
    fake_result = JwtVerifyResult(ok=True, claims={"sub": "x"}, host=None)

    with patch(
        "asap.transport._auth_helpers.verify_host_jwt",
        new=AsyncMock(return_value=fake_result),
    ):
        result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 400
    assert err.body == b'{"detail":"missing iss in host JWT"}'


@pytest.mark.asyncio
async def test_verify_host_bearer_defensive_revoked_host_status_returns_403() -> None:
    """If verify_host_jwt ever returns ok=True for a revoked host, still reject."""
    host_sk = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    pub = ed25519_public_jwk(host_sk)
    revoked_host = HostIdentity(
        host_id="host-revoked",
        public_key=pub,
        status="revoked",
        created_at=now,
        updated_at=now,
    )
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer synthetic")
    fake_result = JwtVerifyResult(
        ok=True,
        claims={"iss": "urn:asap:host:thumb"},
        host=revoked_host,
    )

    with patch(
        "asap.transport._auth_helpers.verify_host_jwt",
        new=AsyncMock(return_value=fake_result),
    ):
        result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 403
    assert err.body == b'{"detail":"host revoked"}'


@pytest.mark.asyncio
async def test_verify_host_bearer_require_active_host_false_skips_revoked_403() -> None:
    """Routes that opt out of host-liveness still get 401 for revoked tokens."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer synthetic")
    fake_result = JwtVerifyResult(ok=False, error=HOST_REVOKED_ERROR)

    with patch(
        "asap.transport._auth_helpers.verify_host_jwt",
        new=AsyncMock(return_value=fake_result),
    ):
        result, err = await verify_host_bearer(
            request,
            jti_replay_cache=None,
            require_active_host=False,
        )

    assert result is None
    assert err is not None
    assert err.status_code == 401
    body: dict[str, Any] = json.loads(err.body)
    assert body["detail"] == HOST_REVOKED_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("iss", ["", "   ", 42])
async def test_verify_host_bearer_blank_or_non_string_iss_returns_400(iss: object) -> None:
    """Whitespace and non-string iss values are missing issuers, not authenticated hosts."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer synthetic")
    fake_result = JwtVerifyResult(ok=True, claims={"iss": iss}, host=None)

    with patch(
        "asap.transport._auth_helpers.verify_host_jwt",
        new=AsyncMock(return_value=fake_result),
    ):
        result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 400
    assert err.body == b'{"detail":"missing iss in host JWT"}'


@pytest.mark.asyncio
async def test_verify_host_bearer_success_without_claims_returns_401() -> None:
    """ok=True with no claims must not authenticate; the iss gate cannot run."""
    app = _identity_app(InMemoryHostStore())
    request = _http_request(app, authorization="Bearer synthetic")
    fake_result = JwtVerifyResult(ok=True, claims=None, host=None)

    with patch(
        "asap.transport._auth_helpers.verify_host_jwt",
        new=AsyncMock(return_value=fake_result),
    ):
        result, err = await verify_host_bearer(request, jti_replay_cache=None)

    assert result is None
    assert err is not None
    assert err.status_code == 401
    assert err.body == b'{"detail":"Invalid host token"}'
