"""Regression: AgentStore get→mutate→full-row save must not resurrect revoke.

Distinct from verify_agent_jwt LIFE-005 TOCTOU (PR #323): rotate-key, reactivate,
and status pending→active / pending→rejected approval writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_host_jwt
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
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


class _RevokeInsideSaveAgentStore(InMemoryAgentStore):
    """Revokes the existing row on the next non-revoked ``save`` (save-time race)."""

    def __init__(self) -> None:
        super().__init__()
        self.revoke_on_next_save = False

    async def save(self, agent: AgentSession) -> None:
        if self.revoke_on_next_save and agent.status != "revoked":
            existing = self._agents.get(agent.agent_id)
            if existing is not None:
                self.revoke_on_next_save = False
                await self.revoke(agent.agent_id)
        await super().save(agent)


class _SaveRaisesGenericValueErrorStore(InMemoryAgentStore):
    """Raises a generic ``ValueError`` on the next save (not a revoke overwrite)."""

    def __init__(self) -> None:
        super().__init__()
        self.raise_on_next_save = False

    async def save(self, agent: AgentSession) -> None:
        if self.raise_on_next_save:
            self.raise_on_next_save = False
            msg = "disk full"
            raise ValueError(msg)
        await super().save(agent)


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


def _register_agent(
    client: TestClient,
    host_sk: Ed25519PrivateKey,
    *,
    extra_json: dict[str, Any] | None = None,
) -> str:
    agent_sk = Ed25519PrivateKey.generate()
    reg_tok = create_host_jwt(
        host_sk,
        aud=_HOST_JWT_AUDIENCE,
        agent_public_key=ed25519_public_jwk(agent_sk),
        ttl_seconds=120,
    )
    kwargs: dict[str, Any] = {"headers": {"Authorization": f"Bearer {reg_tok}"}}
    if extra_json is not None:
        kwargs["json"] = extra_json
    reg = client.post("/asap/agent/register", **kwargs)
    assert reg.status_code == 200
    return str(reg.json()["agent_id"])


def _host_token(host_sk: Ed25519PrivateKey) -> str:
    return create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRevokeResurrectionRaces:
    """HTTP paths that must not resurrect a concurrent revoke via full-row save."""

    async def test_rotate_key_does_not_resurrect_revoked_agent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Pre-save re-read on rotate-key must keep concurrent revoke."""
        agent_store = _RevokeOnNthGetAgentStore(revoke_on_get=2)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk)
        sess = await agent_store.get(aid)
        assert sess is not None
        agent_store._get_calls = 0
        await agent_store.save(sess.model_copy(update={"status": "active"}))

        rot = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
            json={
                "agent_id": aid,
                "new_public_key": ed25519_public_jwk(Ed25519PrivateKey.generate()),
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
        agent_store = _RevokeOnNthGetAgentStore(revoke_on_get=2)
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk)
        sess = await agent_store.get(aid)
        assert sess is not None
        agent_store._get_calls = 0
        await agent_store.save(sess.model_copy(update={"status": "expired"}))

        resp = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
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
        client = TestClient(app)
        aid = _register_agent(client, host_sk, extra_json={"capabilities": ["file:read"]})
        await app.state.identity_approval_store.approve(aid, "user-1")
        sess = await agent_store.get(aid)
        assert sess is not None and sess.status == "pending"
        agent_store._get_calls = 0

        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "revoked"
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRevokeDuringSave:
    """Save-time guard: concurrent revoke inside ``save()`` must win."""

    async def test_rotate_key_save_time_revoke_keeps_row_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Rotate must hit ``save`` and still leave the agent revoked."""
        agent_store = _RevokeInsideSaveAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk)
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "active"}))
        agent_store.revoke_on_next_save = True

        rot = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
            json={
                "agent_id": aid,
                "new_public_key": ed25519_public_jwk(Ed25519PrivateKey.generate()),
            },
        )
        assert rot.status_code == 400
        assert "revoked" in rot.json()["detail"]
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_reactivate_save_time_revoke_keeps_row_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Reactivate persist must not overwrite a revoke that lands in ``save``."""
        agent_store = _RevokeInsideSaveAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk)
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "expired"}))
        agent_store.revoke_on_next_save = True

        resp = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
            json={"agent_id": aid},
        )
        assert resp.status_code == 403
        assert "revoked" in resp.json()["detail"]
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_status_approval_save_time_revoke_keeps_row_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Approved activation write must not resurrect a revoke inside ``save``."""
        agent_store = _RevokeInsideSaveAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk, extra_json={"capabilities": ["file:read"]})
        await app.state.identity_approval_store.approve(aid, "user-1")
        agent_store.revoke_on_next_save = True

        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "revoked"
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_status_denied_save_time_revoke_keeps_row_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Denied pending→rejected write must not overwrite a concurrent revoke."""
        agent_store = _RevokeInsideSaveAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk, extra_json={"capabilities": ["file:read"]})
        await app.state.identity_approval_store.deny(aid, "denied in test")
        agent_store.revoke_on_next_save = True

        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "revoked"
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "revoked"

    async def test_register_activation_save_time_revoke_does_not_500(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Auto-approve register's second save must map overwrite to a lifecycle error."""
        agent_store = _RevokeInsideSaveAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        host_pub = ed25519_public_jwk(host_sk)
        now = datetime.now(timezone.utc)
        await app.state.identity_host_store.save(
            HostIdentity(
                host_id=host_urn_from_thumbprint(jwk_thumbprint_sha256(host_pub)),
                public_key=dict(host_pub),
                status="active",
                default_capabilities=["exec:read"],
                created_at=now,
                updated_at=now,
            )
        )
        agent_store.revoke_on_next_save = True
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        reg = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"capabilities": ["exec:read"]},
        )
        assert reg.status_code == 400
        assert "revoked" in str(reg.json()["detail"])
        rows = list(agent_store._agents.values())
        assert len(rows) == 1 and rows[0].status == "revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentSaveErrorMapping:
    """Lifecycle writes must not map arbitrary save() ValueError to revoke."""

    async def test_rotate_key_generic_save_value_error_is_not_revoke_message(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """A non-overwrite ``ValueError`` from save must not look like revocation."""
        agent_store = _SaveRaisesGenericValueErrorStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk)
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "active"}))
        agent_store.raise_on_next_save = True

        with pytest.raises(ValueError, match="disk full"):
            client.post(
                "/asap/agent/rotate-key",
                headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
                json={
                    "agent_id": aid,
                    "new_public_key": ed25519_public_jwk(Ed25519PrivateKey.generate()),
                },
            )

    async def test_status_registry_value_error_is_not_swallowed(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Capability grant failures after a successful activation must propagate."""
        agent_store = InMemoryAgentStore()
        app = _app_with_store(sample_manifest, isolated_rate_limiter, agent_store)
        host_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = _register_agent(client, host_sk, extra_json={"capabilities": ["file:read"]})
        await app.state.identity_approval_store.approve(aid, "user-1")

        def _boom(*_args: object, **_kwargs: object) -> list[dict[str, str]]:
            msg = "grant failed for test"
            raise ValueError(msg)

        monkeypatch.setattr(
            "asap.transport.agent_routes.apply_capability_specs_to_registry",
            _boom,
        )
        with pytest.raises(ValueError, match="grant failed for test"):
            client.get(
                f"/asap/agent/status?agent_id={aid}",
                headers={"Authorization": f"Bearer {_host_token(host_sk)}"},
            )
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "active"
