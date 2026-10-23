"""Tests for Custom Claims identity binding.

Covers: custom claim match/mismatch, allowlist fallback, malformed ASAP_AUTH_SUBJECT_MAP.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request
from joserfc import jwk, jwt as jose_jwt
from starlette.testclient import TestClient

from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM, OAuth2Middleware


def _minimal_app(
    manifest_id: str = "urn:asap:agent:bot",
    custom_claim: str | None = None,
    jwks_fetcher: object = None,
) -> FastAPI:
    """Minimal FastAPI app with OAuth2Middleware and identity binding."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def _jwks(_uri: str) -> jwk.KeySet:
        return key_set

    fetcher = jwks_fetcher or _jwks
    app = FastAPI()

    @app.get("/asap")
    async def asap_route(request: Request) -> dict[str, str]:
        claims = getattr(request.state, "oauth2_claims", None)
        return {"status": "ok", "sub": claims.sub if claims else ""}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        required_scope="asap:execute",
        path_prefix="/asap",
        manifest_id=manifest_id,
        custom_claim=custom_claim,
        jwks_fetcher=fetcher,
    )
    return app


def _make_token(
    key: jwk.RSAKey,
    sub: str = "auth0|abc123",
    scope: str = "asap:execute",
    extra_claims: dict | None = None,
) -> str:
    """Build a signed JWT with sub, scope, exp."""
    now = int(time.time())
    claims: dict = {
        "sub": sub,
        "scope": scope,
        "exp": now + 3600,
        "iat": now,
    }
    if extra_claims:
        claims.update(extra_claims)
    return jose_jwt.encode({"alg": "RS256", "typ": "JWT"}, claims, key)


def test_custom_claim_present_and_matches_manifest_succeeds() -> None:
    """Custom claim present and matches manifest id → 200."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(
        key,
        sub="auth0|abc123",
        extra_claims={DEFAULT_CUSTOM_CLAIM: "urn:asap:agent:bot"},
    )

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_custom_claim_present_but_mismatches_returns_403() -> None:
    """Custom claim present but value differs from manifest id → 403."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(
        key,
        sub="auth0|abc123",
        extra_claims={DEFAULT_CUSTOM_CLAIM: "urn:asap:agent:other"},
    )

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Identity mismatch: custom claim does not match agent manifest (expected: urn:asap:agent:bot)"
    )


def test_custom_claim_missing_allowlist_hit_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Custom claim missing, sub in allowlist → 200 and allowlist warning logged."""
    monkeypatch.setenv(
        "ASAP_AUTH_SUBJECT_MAP",
        '{"urn:asap:agent:bot": "auth0|abc123"}',
    )

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with caplog.at_level("WARNING"), TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "identity_via_allowlist" in caplog.text or "allowlist" in caplog.text.lower()


def test_custom_claim_missing_allowlist_miss_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom claim missing, sub not in allowlist → 403."""
    monkeypatch.setenv(
        "ASAP_AUTH_SUBJECT_MAP",
        '{"urn:asap:agent:bot": "auth0|other"}',
    )

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert "Identity mismatch" in response.json()["detail"]


def test_custom_claim_missing_no_allowlist_returns_403() -> None:
    """Custom claim missing and no allowlist entry → 403."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_custom_claim_missing_allowlist_list_value_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom claim missing, sub in allowlist list → 200."""
    monkeypatch.setenv(
        "ASAP_AUTH_SUBJECT_MAP",
        '{"urn:asap:agent:bot": ["auth0|one", "auth0|two"]}',
    )

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|two")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200


def test_asap_auth_subject_map_non_dict_json_treated_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASAP_AUTH_SUBJECT_MAP with valid JSON but non-dict (e.g. []) → allowlist empty."""
    monkeypatch.setenv("ASAP_AUTH_SUBJECT_MAP", "[]")

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_asap_auth_subject_map_invalid_value_type_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASAP_AUTH_SUBJECT_MAP with non-str/list value → filtered out → 403."""
    monkeypatch.setenv("ASAP_AUTH_SUBJECT_MAP", '{"urn:asap:agent:bot": 123}')

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_malformed_asap_auth_subject_map_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed ASAP_AUTH_SUBJECT_MAP → 403."""
    monkeypatch.setenv("ASAP_AUTH_SUBJECT_MAP", "not valid json {")

    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_identity_binding_disabled_when_manifest_id_none() -> None:
    """When manifest_id is None, identity binding is skipped; no custom claim required."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app_without_id = FastAPI()

    @app_without_id.get("/asap")
    async def route(request: Request) -> dict[str, str]:
        claims = getattr(request.state, "oauth2_claims", None)
        return {"status": "ok", "sub": claims.sub if claims else ""}

    app_without_id.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        path_prefix="/asap",
        manifest_id=None,
        jwks_fetcher=jwks,
    )

    token = _make_token(key, sub="auth0|abc123")

    with TestClient(app_without_id) as client:
        response = client.get(
            "/asap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200


def test_custom_claim_whitespace_is_stripped_before_compare() -> None:
    """Padding around the manifest id still binds; padding does not allow a different id."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def jwks(_: str) -> jwk.KeySet:
        return key_set

    app = _minimal_app(manifest_id="urn:asap:agent:bot", jwks_fetcher=jwks)
    match = _make_token(
        key,
        extra_claims={DEFAULT_CUSTOM_CLAIM: "  urn:asap:agent:bot  "},
    )
    mismatch = _make_token(
        key,
        extra_claims={DEFAULT_CUSTOM_CLAIM: "  urn:asap:agent:other  "},
    )

    with TestClient(app) as client:
        allowed = client.get("/asap", headers={"Authorization": f"Bearer {match}"})
        denied = client.get("/asap", headers={"Authorization": f"Bearer {mismatch}"})

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["detail"] == (
        "Identity mismatch: custom claim does not match agent manifest "
        "(expected: urn:asap:agent:bot)"
    )
