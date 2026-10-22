"""Integration tests for OAuth2 token validation middleware.

Covers: missing token (401), expired token (401), insufficient scope (403),
valid token with correct scope (200), JWKS unavailable (503), invalid token
after refetch (401), missing/invalid sub (401). Uses joserfc for JWT signing
and jwks_fetcher for JWKS mocking.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
import pytest
from fastapi import FastAPI, Request
from joserfc import jwk, jwt as jose_jwt
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Middleware, path_under_prefix


def _minimal_app() -> FastAPI:
    """Minimal FastAPI app that returns 200 on GET /asap and GET /other."""
    app = FastAPI()

    @app.get("/asap")
    async def asap() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/other")
    async def other() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_path_under_prefix_matches_exact_and_subpaths() -> None:
    """Operator/registry prefixes must not match similarly named siblings."""
    assert path_under_prefix("/usage", "/usage") is True
    assert path_under_prefix("/usage/export", "/usage") is True
    assert path_under_prefix("/usagex", "/usage") is False
    assert path_under_prefix("/audit", "/audit") is True
    assert path_under_prefix("/asap/agent/status", "/asap") is True


def test_oauth2_middleware_validates_extra_path_prefixes() -> None:
    """extra_path_prefixes protect operator paths outside path_prefix."""
    app = _minimal_app()

    @app.get("/usage")
    async def usage() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        extra_path_prefixes=("/usage",),
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        unprotected = client.get("/other")
        protected = client.get("/usage")

    assert unprotected.status_code == 200
    assert protected.status_code == 401


def test_oauth2_middleware_skips_host_jwt_agent_routes() -> None:
    """``/asap/agent/*`` stays on Host JWT; sibling paths under ``/asap`` do not."""
    app = FastAPI()

    @app.get("/asap")
    async def asap() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/asap/agent/register")
    async def register() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/asap/agentx")
    async def sibling() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        identity = client.get("/asap/agent/register")
        sibling_response = client.get("/asap/agentx")
        protected = client.get("/asap")

    assert identity.status_code == 200
    assert sibling_response.status_code == 401
    assert protected.status_code == 401


_cached_mock_jwks: jwk.KeySet | None = None


async def _mock_jwks_fetcher(_uri: str) -> jwk.KeySet:
    """Return a minimal RSA key set for testing. Cached to avoid per-request key generation."""
    global _cached_mock_jwks
    if _cached_mock_jwks is not None:
        return _cached_mock_jwks
    key = jwk.RSAKey.generate_key(2048, private=True)
    _cached_mock_jwks = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
    return _cached_mock_jwks


def test_oauth2_middleware_rejects_request_without_bearer_token() -> None:
    """Verify OAuth2Middleware returns 401 when Authorization header is missing."""
    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/.well-known/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        response = client.get("/asap")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_oauth2_middleware_rejects_request_with_wrong_scheme() -> None:
    """Verify OAuth2Middleware returns 401 when Authorization is not Bearer."""
    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_oauth2_middleware_passes_through_unprotected_path() -> None:
    """Verify requests to path outside path_prefix are not validated."""
    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        response = client.get("/other")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_oauth2_middleware_accepts_valid_jwt_with_required_scope() -> None:
    """Verify valid JWT with required scope returns 200 and sets oauth2_claims."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = FastAPI()

    @app.get("/asap/me")
    async def asap_me(request: Request) -> dict[str, Any]:
        claims = getattr(request.state, "oauth2_claims", None)
        if claims is None:
            return {"claims": None}
        return {"claims": {"sub": claims.sub, "scope": claims.scope, "exp": claims.exp}}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        required_scope="asap:execute",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    # Build valid JWT with sub, scope, exp
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": "urn:asap:agent:test-client",
        "scope": "asap:read asap:execute",
        "exp": now + 3600,
        "iat": now,
    }
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["claims"]["sub"] == "urn:asap:agent:test-client"
    assert "asap:execute" in data["claims"]["scope"]


def test_oauth2_middleware_accepts_jwt_with_scope_array() -> None:
    """JWT scope as JSON array (not space-separated string) passes required_scope check."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = FastAPI()

    @app.get("/asap/me")
    async def asap_me(request: Request) -> dict[str, Any]:
        claims = getattr(request.state, "oauth2_claims", None)
        if claims is None:
            return {"claims": None}
        return {"claims": {"sub": claims.sub, "scope": claims.scope, "exp": claims.exp}}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        required_scope="asap:execute",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": "urn:asap:agent:array-scope-client",
        "scope": ["asap:read", "asap:execute"],
        "exp": now + 3600,
        "iat": now,
    }
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["claims"]["sub"] == "urn:asap:agent:array-scope-client"
    assert "asap:execute" in data["claims"]["scope"]


def test_oauth2_middleware_rejects_expired_token() -> None:
    """Verify OAuth2Middleware returns 401 when JWT is expired."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": "urn:asap:agent:expired",
        "scope": "asap:execute",
        "exp": now - 3600,  # Expired 1 hour ago
        "iat": now - 7200,
    }
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_returns_403_when_scope_insufficient() -> None:
    """Verify JWT without required scope returns 403."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        required_scope="asap:admin",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"sub": "urn:asap:agent:client", "scope": "asap:read asap:execute", "exp": now + 3600}
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient scope"}


@pytest.mark.parametrize("scope_claim", [42, {"asap:execute": True}])
def test_oauth2_middleware_rejects_non_string_scope_claim(scope_claim: object) -> None:
    """A non-string, non-list ``scope`` claim fail-closes to insufficient scope."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        required_scope="asap:execute",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:weird-scope",
            "scope": scope_claim,
            "exp": now + 3600,
            "iat": now,
        },
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient scope"}


def test_oauth2_middleware_skips_global_scope_on_extra_prefixes() -> None:
    """Operator prefixes keep their own scope check and do not inherit ``asap:admin``."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = FastAPI()

    @app.get("/usage")
    async def usage() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/asap")
    async def asap() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        required_scope="asap:admin",
        extra_path_prefixes=("/usage",),
        jwks_fetcher=jwks_fetcher,
    )
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:operator", "scope": "asap:execute", "exp": now + 3600, "iat": now},
        key,
    )
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        operator = client.get("/usage", headers=headers)
        protocol = client.get("/asap", headers=headers)
        anonymous = client.get("/usage")

    assert operator.status_code == 200
    assert protocol.status_code == 403
    assert protocol.json() == {"detail": "Insufficient scope"}
    assert anonymous.status_code == 401


async def test_oauth2_middleware_returns_503_when_jwks_fetch_fails() -> None:
    """When JWKS fetcher raises HTTPError, middleware returns 503."""

    async def jwks_fetcher_fail(_uri: str) -> jwk.KeySet:
        raise httpx.ConnectError("JWKS endpoint unreachable")

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher_fail,
    )

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.x"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable"}


async def test_oauth2_middleware_returns_503_when_jwks_refetch_fails_after_invalid_token() -> None:
    """First decode raises JoseError; refetch JWKS raises HTTPError -> 503."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
    call_count = 0

    async def jwks_fetcher_first_ok_then_fail(_uri: str) -> jwk.KeySet:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return key_set
        raise httpx.ConnectError("JWKS unreachable on refetch")

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher_first_ok_then_fail,
    )

    other_key = jwk.RSAKey.generate_key(2048, private=True)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "test", "exp": now + 3600},
        other_key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable"}


async def test_oauth2_middleware_returns_401_when_token_invalid_after_refetch() -> None:
    """First decode raises JoseError; refetch succeeds but decode still fails -> 401."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
    other_key = jwk.RSAKey.generate_key(2048, private=True)

    async def jwks_fetcher_always_return_wrong_key(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher_always_return_wrong_key,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "test", "exp": now + 3600},
        other_key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}
    assert "WWW-Authenticate" in response.headers


def test_oauth2_middleware_returns_401_when_sub_missing() -> None:
    """JWT without 'sub' claim is rejected with 401."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"exp": now + 3600, "scope": "asap:execute"},
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_returns_401_when_sub_not_string() -> None:
    """JWT with non-string 'sub' (e.g. number) is rejected with 401."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": 12345, "exp": now + 3600, "scope": "asap:execute"},
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_accepts_token_without_iss_aud_when_not_configured() -> None:
    """When expected_issuer/audience are unset, tokens without iss/aud still pass."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:client", "scope": "asap:execute", "exp": now + 3600},
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


_EXPECTED_ISSUER = "https://auth.example.com"
_EXPECTED_AUDIENCE = "urn:asap:agent:test-server"


def _oauth2_app_with_iss_aud_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    via_env: bool,
) -> tuple[FastAPI, jwk.RSAKey]:
    if via_env:
        monkeypatch.setenv("ASAP_AUTH_ISSUER", _EXPECTED_ISSUER)
        monkeypatch.setenv("ASAP_AUTH_AUDIENCE", _EXPECTED_AUDIENCE)

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    middleware_kwargs: dict[str, Any] = {
        "jwks_uri": f"{_EXPECTED_ISSUER}/jwks.json",
        "path_prefix": "/asap",
        "jwks_fetcher": jwks_fetcher,
    }
    if not via_env:
        middleware_kwargs["expected_issuer"] = _EXPECTED_ISSUER
        middleware_kwargs["expected_audience"] = _EXPECTED_AUDIENCE

    app = _minimal_app()
    app.add_middleware(OAuth2Middleware, **middleware_kwargs)
    return app, key


@pytest.mark.parametrize(
    ("via_env", "claim_field"),
    [
        (False, "iss"),
        (False, "aud"),
        (True, "iss"),
        (True, "aud"),
    ],
    ids=["constructor-wrong-iss", "constructor-wrong-aud", "env-wrong-iss", "env-wrong-aud"],
)
def test_oauth2_middleware_rejects_mismatched_iss_or_aud_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    via_env: bool,
    claim_field: Literal["iss", "aud"],
) -> None:
    """When iss/aud is configured, mismatched claims return 401."""
    app, key = _oauth2_app_with_iss_aud_config(monkeypatch, via_env=via_env)
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "urn:asap:agent:client",
        "scope": "asap:execute",
        "exp": now + 3600,
        "iss": _EXPECTED_ISSUER,
        "aud": _EXPECTED_AUDIENCE,
    }
    if claim_field == "iss":
        claims["iss"] = "https://evil.example.com"
    else:
        claims["aud"] = "wrong-audience"

    token = jose_jwt.encode({"alg": "RS256", "typ": "JWT"}, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_accepts_token_without_exp_claim() -> None:
    """Middleware uses require_exp=False; tokens lacking exp still pass when otherwise valid."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:client", "scope": "asap:execute"},
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_oauth2_middleware_rejects_non_numeric_exp_claim() -> None:
    """Non-numeric exp must be rejected (parity with Host JWT expiry validation)."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:client",
            "scope": "asap:execute",
            "exp": "not-a-number",
            "iat": now,
        },
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_rejects_string_numeric_past_exp() -> None:
    """String-typed NumericDate exp in the past must not bypass expiry checks."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    past = str(int(time.time()) - 3600)
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:expired",
            "scope": "asap:execute",
            "exp": past,
            "iat": int(time.time()) - 7200,
        },
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


def test_oauth2_middleware_rejects_zero_exp_claim() -> None:
    """exp:0 is epoch NumericDate and must not bypass expiry checks."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:expired",
            "scope": "asap:execute",
            "exp": 0,
            "iat": now - 7200,
        },
        key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication token"}


async def test_oauth2_middleware_refetches_jwks_on_key_rotation_success() -> None:
    """Stale cached key triggers refetch; token signed with rotated key succeeds."""
    correct_key = jwk.RSAKey.generate_key(2048, private=True)
    wrong_key = jwk.RSAKey.generate_key(2048, private=True)
    correct_key_set = jwk.KeySet.import_key_set({"keys": [correct_key.as_dict(private=False)]})
    wrong_key_set = jwk.KeySet.import_key_set({"keys": [wrong_key.as_dict(private=False)]})
    call_count = 0

    async def jwks_fetcher_rotation(_uri: str) -> jwk.KeySet:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return wrong_key_set
        return correct_key_set

    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=jwks_fetcher_rotation,
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:client", "scope": "asap:execute", "exp": now + 3600},
        correct_key,
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert call_count == 2


@pytest.mark.parametrize("via_env", [False, True], ids=["constructor", "env"])
def test_oauth2_middleware_accepts_matching_iss_aud_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    via_env: bool,
) -> None:
    """When iss/aud is configured, matching claims return 200."""
    app, key = _oauth2_app_with_iss_aud_config(monkeypatch, via_env=via_env)
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "urn:asap:agent:client",
        "scope": "asap:execute",
        "exp": now + 3600,
        "iss": _EXPECTED_ISSUER,
        "aud": _EXPECTED_AUDIENCE,
    }
    token = jose_jwt.encode({"alg": "RS256", "typ": "JWT"}, claims, key)

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_oauth2_middleware_rejects_empty_bearer_token() -> None:
    """Authorization: Bearer with no token value must return 401."""
    app = _minimal_app()
    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        jwks_fetcher=lambda uri: _mock_jwks_fetcher(uri),
    )

    with TestClient(app) as client:
        response = client.get("/asap", headers={"Authorization": "Bearer "})

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
