"""Delegation routes must authorize the full agent URN.

Storage lookups are already exact. These routes compare the OAuth subject
with the stored delegator and delegate. A prefix, case change, or trimmed
copy must not read, list, or revoke another agent's token. The holder may
read the one token, and only the delegator may revoke it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.testclient import TestClient
from joserfc import jwk
from joserfc import jwt as jose_jwt

from asap.auth import OAuth2Config
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM
from asap.crypto.keys import generate_keypair
from asap.economics.delegation import get_jti_from_jwt
from asap.economics.delegation_storage import InMemoryDelegationStorage
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.transport.server import create_app

_MANIFEST_ID = "urn:asap:agent:test-server"
_ISSUER = "urn:asap:agent:bot-01"
_HOLDER = "urn:asap:agent:worker-01"
_VIEW_DENIED_DETAIL = "Only the issuer or holder of this token can view it"
_REVOKE_DENIED_DETAIL = "Only the delegator that issued this token can revoke it"
_NEIGHBORS = (
    "urn:asap:agent:bot-010",
    "urn:asap:agent:bot-0",
    "urn:asap:agent:bot",
    "URN:asap:agent:bot-01",
    " urn:asap:agent:bot-01",
    "urn:asap:agent:bot-01 ",
    "urn:asap:agent:worker-010",
    "urn:asap:agent:worker-0",
    "urn:asap:agent:worker",
    "URN:asap:agent:worker-01",
    " urn:asap:agent:worker-01",
    "urn:asap:agent:worker-01 ",
)


def _manifest() -> Manifest:
    """Server manifest whose id matches the OAuth custom claim."""
    return Manifest(
        id=_MANIFEST_ID,
        name="Test Server",
        version="1.0.0",
        description="Delegation subject fixture",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _oauth2_bearer_token(oauth2_key: jwk.RSAKey, subject: str) -> str:
    """Bearer token whose subject is ``subject`` and whose scope can call the API."""
    now = int(time.time())
    return jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": subject,
            "scope": "asap:execute",
            "exp": now + 3600,
            DEFAULT_CUSTOM_CLAIM: _MANIFEST_ID,
        },
        oauth2_key,
    )


def _delegation_app() -> tuple[Any, InMemoryDelegationStorage, jwk.RSAKey]:
    """App with one signing key and empty in-memory delegation storage."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
    private_key, _public_key = generate_keypair()
    storage = InMemoryDelegationStorage()

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return key_set

    def key_store(_delegator_urn: str) -> Any:
        return private_key

    app = create_app(
        _manifest(),
        oauth2_config=OAuth2Config(
            jwks_uri="https://auth.example.com/jwks.json",
            path_prefix="/asap",
            jwks_fetcher=jwks_fetcher,
        ),
        delegation_key_store=key_store,
        delegation_storage=storage,
        rate_limit="999999/minute",
    )
    return app, storage, key


def _auth_header(bearer: str) -> dict[str, str]:
    """Authorization header for ``bearer``."""
    return {"Authorization": f"Bearer {bearer}"}


def _assert_visible(client: TestClient, token_id: str, bearer: str) -> None:
    """The exact issuer and the exact holder can read this token."""
    response = client.get(
        f"/asap/delegations/{token_id}",
        headers=_auth_header(bearer),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == token_id
    assert body["delegator"] == _ISSUER
    assert body["delegate"] == _HOLDER
    assert body["active"] is True


def _assert_view_denied(client: TestClient, token_id: str, bearer: str) -> None:
    """A neighboring subject cannot read the token."""
    response = client.get(
        f"/asap/delegations/{token_id}",
        headers=_auth_header(bearer),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == _VIEW_DENIED_DETAIL


def _listed_token_ids(client: TestClient, bearer: str) -> list[str]:
    """Return delegation ids listed for ``bearer``."""
    response = client.get("/asap/delegations", headers=_auth_header(bearer))
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    return [str(item["id"]) for item in items]


def _assert_revoke_denied(client: TestClient, token_id: str, bearer: str) -> None:
    """A subject other than the exact delegator cannot revoke the token."""
    response = client.delete(
        f"/asap/delegations/{token_id}",
        headers=_auth_header(bearer),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == _REVOKE_DENIED_DETAIL


def _assert_neighbors_denied(
    client: TestClient,
    token_id: str,
    oauth2_key: jwk.RSAKey,
) -> None:
    """Prefix, case, and padding miss view, list, and revoke."""
    for subject in _NEIGHBORS:
        bearer = _oauth2_bearer_token(oauth2_key, subject)
        _assert_view_denied(client, token_id, bearer)
        assert _listed_token_ids(client, bearer) == []
        _assert_revoke_denied(client, token_id, bearer)


def test_delegation_route_subject_match_is_exact() -> None:
    """OAuth subject equality on delegation routes is exact and case-sensitive.

    The holder can read the token and cannot revoke it. A neighbor of the
    issuer or the holder cannot read it, list it, or revoke it.
    """
    app, storage, oauth2_key = _delegation_app()
    issuer = _oauth2_bearer_token(oauth2_key, _ISSUER)
    holder = _oauth2_bearer_token(oauth2_key, _HOLDER)

    with TestClient(app) as client:
        created = client.post(
            "/asap/delegations",
            json={"delegate": _HOLDER, "scopes": ["task.execute"]},
            headers=_auth_header(issuer),
        )
        assert created.status_code == 201
        token_id = get_jti_from_jwt(created.json()["token"])
        assert isinstance(token_id, str) and token_id != ""

        _assert_visible(client, token_id, issuer)
        _assert_visible(client, token_id, holder)
        assert _listed_token_ids(client, issuer) == [token_id]
        assert _listed_token_ids(client, holder) == []
        _assert_revoke_denied(client, token_id, holder)
        _assert_neighbors_denied(client, token_id, oauth2_key)

    assert asyncio.run(storage.is_revoked(token_id)) is False
