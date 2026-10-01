"""Regression: pending→active / reactivate full-row save must not undo rotate-key.

Distinct from revoke resurrection (``test_agent_revoke_race.py`` / LIFE-005):
a concurrent ``POST /asap/agent/rotate-key`` lands a new JWK, then status or
reactivate persists a snapshot that still carries the old ``public_key``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_host_jwt
from asap.auth.identity import (
    AgentSession,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from asap.transport.server import create_app
from tests.crypto.jwk_helpers import ed25519_public_jwk

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_HOST_JWT_AUDIENCE = "urn:asap:agent:test-server"


def _auth_header(host_sk: Ed25519PrivateKey) -> dict[str, str]:
    """Host JWT Authorization header for identity HTTP tests."""
    token = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
    return {"Authorization": f"Bearer {token}"}


class _RotateOnArmedSaveAgentStore(InMemoryAgentStore):
    """Applies a concurrent key rotation on the next non-revoked ``save``."""

    def __init__(self, new_public_key: dict[str, Any]) -> None:
        super().__init__()
        self._armed = False
        self._new_public_key = new_public_key

    def arm_rotate_on_save(self) -> None:
        """Rotate the stored JWK just before the next lifecycle persist."""
        self._armed = True

    async def save(
        self,
        agent: AgentSession,
        *,
        expected_public_key: dict[str, Any] | None = None,
    ) -> None:
        if self._armed and agent.status != "revoked":
            current = self._agents[agent.agent_id]
            await super().save(current.model_copy(update={"public_key": self._new_public_key}))
            self._armed = False
        if expected_public_key is None:
            await super().save(agent)
            return
        await super().save(agent, expected_public_key=expected_public_key)


def _app_with_store(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    agent_store: InMemoryAgentStore,
) -> FastAPI:
    host_store = InMemoryHostStore(agent_store=agent_store)
    app = create_app(
        sample_manifest,
        rate_limit="999999/minute",
        identity_host_store=host_store,
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    return app


def _register_pending_file_read(
    client: TestClient,
    host_sk: Ed25519PrivateKey,
    agent_sk: Ed25519PrivateKey,
) -> str:
    """Register with a non-default capability so the session stays pending."""
    reg_tok = create_host_jwt(
        host_sk,
        aud=_HOST_JWT_AUDIENCE,
        agent_public_key=ed25519_public_jwk(agent_sk),
        ttl_seconds=120,
    )
    reg = client.post(
        "/asap/agent/register",
        headers={"Authorization": f"Bearer {reg_tok}"},
        json={"capabilities": ["file:read"]},
    )
    assert reg.status_code == 200
    assert reg.json()["status"] == "pending"
    agent_id = reg.json()["agent_id"]
    assert isinstance(agent_id, str) and agent_id
    return agent_id


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentKeyRotationRaces:
    """HTTP paths that must not revert a concurrent rotate-key via full-row save."""

    async def test_status_approval_activation_does_not_revert_rotated_key(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Approved status poll must keep a JWK that rotated inside ``save``."""
        new_sk = Ed25519PrivateKey.generate()
        new_pub = ed25519_public_jwk(new_sk)
        agent_store = _RotateOnArmedSaveAgentStore(new_pub)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_pending_file_read(client, host_sk, agent_sk)
        await app.state.identity_approval_store.approve(aid, "user-1")
        agent_store.arm_rotate_on_save()

        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers=_auth_header(host_sk),
        )
        assert st.status_code == 200
        stored = await agent_store.get(aid)
        assert stored is not None
        assert jwk_thumbprint_sha256(stored.public_key) == jwk_thumbprint_sha256(new_pub)

    async def test_reactivate_does_not_revert_rotated_key(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Reactivate persist must keep a JWK that rotated inside ``save``."""
        new_sk = Ed25519PrivateKey.generate()
        new_pub = ed25519_public_jwk(new_sk)
        agent_store = _RotateOnArmedSaveAgentStore(new_pub)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "expired"}))
        agent_store.arm_rotate_on_save()

        resp = client.post(
            "/asap/agent/reactivate",
            headers=_auth_header(host_sk),
            json={"agent_id": aid},
        )
        assert resp.status_code in (200, 409)
        stored = await agent_store.get(aid)
        assert stored is not None
        assert jwk_thumbprint_sha256(stored.public_key) == jwk_thumbprint_sha256(new_pub)
