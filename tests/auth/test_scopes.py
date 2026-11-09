"""Tests for scope helpers (parse_scope) and require_scope authorization dependency."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Request
from joserfc import jwk, jwt as jose_jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Claims, OAuth2Middleware
from asap.auth.scopes import SCOPE_ADMIN, SCOPE_EXECUTE, parse_scope, require_scope


# ---------------------------------------------------------------------------
# parse_scope
# ---------------------------------------------------------------------------


def test_parse_scope_none_returns_empty() -> None:
    """parse_scope(None) returns []."""
    assert parse_scope(None) == []


def test_parse_scope_list_returns_strings() -> None:
    """parse_scope(list) returns list of strings."""
    assert parse_scope(["a", "b", "c"]) == ["a", "b", "c"]
    assert parse_scope([1, 2]) == ["1", "2"]


def test_parse_scope_str_splits_and_strips() -> None:
    """parse_scope(str) splits on whitespace and strips."""
    assert parse_scope("asap:read asap:execute") == ["asap:read", "asap:execute"]
    assert parse_scope("  a   b  ") == ["a", "b"]


def test_parse_scope_invalid_type_returns_empty() -> None:
    """parse_scope with non-None, non-list, non-str (e.g. int, dict) returns []."""
    assert parse_scope(123) == []
    assert parse_scope({}) == []
    assert parse_scope(True) == []


def test_parse_scope_empty_string_returns_empty() -> None:
    """parse_scope with empty or whitespace-only strings returns []."""
    assert parse_scope("") == []
    assert parse_scope("   ") == []


# ---------------------------------------------------------------------------
# require_scope
# ---------------------------------------------------------------------------


def test_require_scope_blocks_request_missing_required_scope() -> None:
    """Verify Depends(require_scope(scope)) returns 403 when token lacks that scope."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = FastAPI()

    @app.get("/admin", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
    async def admin_route() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/admin",
        jwks_fetcher=jwks_fetcher,
    )

    # Token has asap:execute but not asap:admin
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": "urn:asap:agent:client",
        "scope": "asap:read asap:execute",
        "exp": now + 3600,
    }
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/admin", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient scope"}


def test_require_scope_allows_request_with_required_scope() -> None:
    """Verify Depends(require_scope(scope)) returns 200 when token has that scope."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app = FastAPI()

    @app.get("/execute")
    async def execute_route(
        claims: OAuth2Claims = Depends(require_scope(SCOPE_EXECUTE)),
    ) -> dict[str, str]:
        return {"sub": claims.sub}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/execute",
        jwks_fetcher=jwks_fetcher,
    )

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"sub": "urn:asap:agent:runner", "scope": "asap:execute", "exp": now + 3600}
    token = jose_jwt.encode(header, claims, key)

    with TestClient(app) as client:
        response = client.get("/execute", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"sub": "urn:asap:agent:runner"}


def test_require_scope_returns_401_when_no_oauth2_claims() -> None:
    """require_scope raises 401 when request.state.oauth2_claims is not set (e.g. route not behind middleware)."""
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_scope(SCOPE_EXECUTE))])
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert "WWW-Authenticate" in response.headers


def test_require_scope_rejects_scope_that_only_contains_the_name() -> None:
    """``asap:admin`` is not granted by a longer name or by the bare suffix ``admin``."""
    app = FastAPI()

    class NeighborScopeMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            request.state.oauth2_claims = OAuth2Claims(
                sub="urn:asap:agent:client",
                scope=["asap:administrator", "admin"],
                exp=0,
            )
            return await call_next(request)

    app.add_middleware(NeighborScopeMiddleware)

    @app.get("/admin", dependencies=[Depends(require_scope(SCOPE_ADMIN))])
    async def admin_route() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/admin")

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient scope"}


def test_require_scope_returns_401_when_oauth2_claims_wrong_type() -> None:
    """require_scope raises 401 when request.state.oauth2_claims is set but not OAuth2Claims."""
    app = FastAPI()

    class FakeStateMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            request.state.oauth2_claims = "not_oauth2_claims"
            return await call_next(request)

    app.add_middleware(FakeStateMiddleware)

    @app.get("/protected", dependencies=[Depends(require_scope(SCOPE_EXECUTE))])
    async def protected() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
