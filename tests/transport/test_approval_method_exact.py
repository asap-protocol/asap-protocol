"""Registration reads ``approval_method`` with equality.

A linked host that can use CIBA stays on CIBA unless the token is exactly
``device_authorization``. A prefix, case change, or padded copy stays on CIBA.
"""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_host_jwt
from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
from asap.models.entities import Manifest
from asap.transport.rate_limit import ASAPRateLimiter
from asap.transport.server import create_app
from tests.crypto.jwk_helpers import ed25519_public_jwk

_AUDIENCE = "urn:asap:agent:test-server"
_EXACT = "device_authorization"
_NEIGHBORS: tuple[str, ...] = (
    "device_authorization_extra",
    "device_authorizatio",
    "Device_Authorization",
    " device_authorization",
    "device_authorization ",
)


def _app(
    manifest: Manifest,
    limiter: ASAPRateLimiter | None,
) -> tuple[FastAPI, InMemoryHostStore]:
    """App whose identity routes use the isolated test limiter."""
    agent_store = InMemoryAgentStore()
    host_store = InMemoryHostStore(agent_store=agent_store)
    app = create_app(
        manifest,
        rate_limit="999999/minute",
        identity_host_store=host_store,
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    app.state.identity_host_supports_ciba = True
    if limiter is not None:
        app.state.limiter = limiter
    return app, host_store


def _register(
    client: TestClient,
    host_key: Ed25519PrivateKey,
    body: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Register one new agent key and return the JSON object."""
    token = create_host_jwt(
        host_key,
        aud=_AUDIENCE,
        agent_public_key=ed25519_public_jwk(Ed25519PrivateKey.generate()),
        ttl_seconds=120,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if body is None:
        response = client.post("/asap/agent/register", headers=headers)
    else:
        response = client.post("/asap/agent/register", headers=headers, json=body)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def test_approval_method_token_is_exact(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
) -> None:
    """The exact token selects the device flow. Lookalikes stay on CIBA."""
    app, host_store = _app(sample_manifest, isolated_rate_limiter)
    host_key = Ed25519PrivateKey.generate()
    client = TestClient(app)

    created = _register(client, host_key)
    host = await host_store.get(str(created["host_id"]))
    assert host is not None
    await host_store.save(host.model_copy(update={"user_id": "linked-user-42"}))

    exact = _register(client, host_key, {"approval_method": _EXACT})
    assert exact["approval"]["method"] == "device_authorization"

    for neighbor in _NEIGHBORS:
        chosen = _register(client, host_key, {"approval_method": neighbor})
        assert chosen["approval"]["method"] == "ciba"
