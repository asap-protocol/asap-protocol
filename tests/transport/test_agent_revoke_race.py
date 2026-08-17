"""Regression: AgentStore get→mutate→full-row save must not resurrect revoke.

Distinct from verify_agent_jwt LIFE-005 TOCTOU (PR #323): rotate-key, reactivate,
and status pending→active approval activation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_host_jwt
from asap.auth.identity import (
    AgentSession,
    InMemoryAgentStore,
    InMemoryHostStore,
)
from asap.transport.server import create_app
from tests.crypto.jwk_helpers import ed25519_public_jwk

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_HOST_JWT_AUDIENCE = "urn:asap:agent:test-server"


class _RevokeOnNthGetAgentStore(InMemoryAgentStore):
    """Revokes the agent on the Nth ``get`` to simulate a concurrent revoke."""

    def __init__(self, revoke_on_get: int) -> None:
        super().__init__()
        self._get_calls = 0
        self._revoke_on_get = revoke_on_get

    async def get(self, agent_id: str) -> AgentSession | None:
        self._get_calls += 1
        if self._get_calls == self._revoke_on_get:
            await self.revoke(agent_id)
        return await super().get(agent_id)


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


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRevokeResurrectionRaces:
    """HTTP paths that must not resurrect a concurrent revoke via full-row save."""

    async def test_rotate_key_does_not_resurrect_revoked_agent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Pre-save re-read on rotate-key must keep concurrent revoke."""
        # get#1: initial load; get#2: pre-save re-read triggers revoke
        agent_store = _RevokeOnNthGetAgentStore(revoke_on_get=2)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        new_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        # Reset counter after setup gets; next handler get is #1 again conceptually —
        # register/status setup already consumed gets. Re-seed store and counter.
        agent_store._get_calls = 0
        await agent_store.save(sess.model_copy(update={"status": "active"}))

        rot_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        rot = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {rot_tok}"},
            json={
                "agent_id": aid,
                "new_public_key": ed25519_public_jwk(new_sk),
            },
        )
        assert rot.status_code == 400
        assert "revoked" in rot.json()["detail"]
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_reactivate_does_not_resurrect_revoked_agent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Reactivate re-read must not overwrite a concurrent revoke."""
        # get#1: ownership load; get#2: pre-mutate re-read triggers revoke
        agent_store = _RevokeOnNthGetAgentStore(revoke_on_get=2)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        agent_store._get_calls = 0
        await agent_store.save(sess.model_copy(update={"status": "expired"}))

        tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        resp = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {tok}"},
            json={"agent_id": aid},
        )
        assert resp.status_code == 403
        assert "revoked" in resp.json()["detail"]
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_status_approval_activation_does_not_resurrect_revoked_agent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Approved status poll must not activate over a concurrent revoke."""
        agent_store = _RevokeOnNthGetAgentStore(revoke_on_get=2)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        # Capabilities outside host defaults force pending approval.
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        reg = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
            json={"capabilities": ["file:read"]},
        )
        assert reg.status_code == 200
        aid = reg.json()["agent_id"]
        assert reg.json()["status"] == "pending"

        approval_store = app.state.identity_approval_store
        await approval_store.approve(aid, "user-1")

        sess = await agent_store.get(aid)
        assert sess is not None and sess.status == "pending"
        agent_store._get_calls = 0

        status_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {status_tok}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "revoked"
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"
