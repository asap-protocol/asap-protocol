"""Lock ``path_prefix=None`` and the root-prefix match.

``None`` is the historic validate-everything mode: every path needs a bearer
token, the required scope still applies, and ``/asap/agent/*`` stays on Host
JWT. A prefix of ``/`` matches every path; without that branch the check
would look for a ``//`` prefix and miss ``/health``.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from joserfc import jwk, jwt as jose_jwt
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Middleware, path_under_prefix


def test_path_under_prefix_root_matches_every_path() -> None:
    """Root, blank, and trailing-slash prefixes use the normalized form."""
    assert path_under_prefix("/health", "/") is True
    assert path_under_prefix("/health", "") is True
    assert path_under_prefix("/health", "///") is True
    assert path_under_prefix("/usage/export", "/usage/") is True
    assert path_under_prefix("/usagex", "/usage/") is False


def _token(key: jwk.RSAKey, scope: str) -> str:
    """Sign a short-lived bearer token with the given scope claim."""
    issued_at = int(time.time())
    return jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:global",
            "scope": scope,
            "exp": issued_at + 3600,
            "iat": issued_at,
        },
        key,
    )


def _app(key_set: jwk.KeySet, *, required_scope: str | None) -> FastAPI:
    """App whose middleware validates every path except agent identity routes."""
    app = FastAPI()

    @app.get("/other")
    async def other() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/asap/agent/register")
    async def register() -> dict[str, str]:
        return {"status": "ok"}

    async def fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix=None,
        required_scope=required_scope,
        jwks_fetcher=fetcher,
    )
    return app


@pytest.fixture(scope="module")
def rsa_key() -> jwk.RSAKey:
    """Return one RSA key for this module.

    Module scope keeps the two HTTP cases from generating a second 2048-bit key.
    """
    return jwk.RSAKey.generate_key(2048, private=True)


def test_path_prefix_none_still_skips_agent_identity_routes(rsa_key: jwk.RSAKey) -> None:
    """Anonymous calls outside ``/asap/agent/*`` are rejected; identity routes stay open."""
    key_set = jwk.KeySet.import_key_set({"keys": [rsa_key.as_dict(private=False)]})
    app = _app(key_set, required_scope=None)

    with TestClient(app) as client:
        anonymous = client.get("/other")
        identity = client.get("/asap/agent/register")

    assert anonymous.status_code == 401
    assert anonymous.json() == {"detail": "Authentication required"}
    assert identity.status_code == 200
    assert identity.json() == {"status": "ok"}


def test_path_prefix_none_applies_required_scope_outside_asap(rsa_key: jwk.RSAKey) -> None:
    """The global scope applies on paths that are not under ``/asap``."""
    key_set = jwk.KeySet.import_key_set({"keys": [rsa_key.as_dict(private=False)]})
    app = _app(key_set, required_scope="asap:execute")
    denied = _token(rsa_key, "asap:read")
    allowed = _token(rsa_key, "asap:read asap:execute")

    with TestClient(app) as client:
        forbidden = client.get("/other", headers={"Authorization": f"Bearer {denied}"})
        accepted = client.get("/other", headers={"Authorization": f"Bearer {allowed}"})

    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Insufficient scope"}
    assert accepted.status_code == 200
    assert accepted.json() == {"status": "ok"}
