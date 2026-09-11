"""Tests for ``POST /asap/agent/request-capability`` (ESC-001..003)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import JwtVerifyResult, create_agent_jwt
from asap.auth.capabilities import CapabilityDefinition
from asap.auth.identity import AgentStore, HostStore, jwk_thumbprint_sha256
from tests.crypto.jwk_helpers import ed25519_public_jwk
from tests.transport.test_capability_routes import (
    _HOST_JWT_AUDIENCE,
    _register_and_activate,
    _setup,
)

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter


async def _activate_host_with_defaults(
    host_store: HostStore,
    agent_store: AgentStore,
    agent_id: str,
    *,
    default_capabilities: list[str],
) -> None:
    """Force host ``active`` and default capability list for escalation policy."""
    sess = await agent_store.get(agent_id)
    assert sess is not None
    host = await host_store.get(sess.host_id)
    assert host is not None
    await host_store.save(
        host.model_copy(
            update={"status": "active", "default_capabilities": list(default_capabilities)},
        ),
    )


def _agent_token(agent_sk: Ed25519PrivateKey, host_sk: Ed25519PrivateKey, agent_id: str) -> str:
    host_tp = jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
    return create_agent_jwt(
        agent_sk,
        host_thumbprint=host_tp,
        agent_id=agent_id,
        aud=_HOST_JWT_AUDIENCE,
        capabilities=["file:read"],
    )


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestEscalationRoutes:
    async def test_all_auto_grant(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="file:write", description="w"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store,
            agent_store,
            aid,
            default_capabilities=["file:read", "file:write"],
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "file:write"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "active"
        assert any(
            g.get("capability") == "file:write" for g in body.get("agent_capability_grants", [])
        )

    async def test_all_need_approval(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store,
            agent_store,
            aid,
            default_capabilities=["file:read"],
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "admin:config"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert "approval" in body

    async def test_mixed_auto_and_approval(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="file:write", description="w"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store,
            agent_store,
            aid,
            default_capabilities=["file:read", "file:write"],
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "file:write"}, {"name": "admin:config"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        grants = body.get("agent_capability_grants") or []
        assert any(g.get("capability") == "file:write" for g in grants)
        assert "approval" in body


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestEscalationRoutesErrorsAndBranches:
    """HTTP error paths and consent / A2H branches on ``request-capability``."""

    async def test_no_authorization_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_rest = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        r = client.post("/asap/agent/request-capability", json={"capabilities": [{"name": "x"}]})
        assert r.status_code == 401

    async def test_invalid_json_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            content=b"{not-json",
        )
        assert r.status_code == 400
        assert "Invalid JSON" in r.json()["detail"]

    async def test_json_body_not_object_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json=[],
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "JSON body must be an object"

    async def test_capabilities_validation_error_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "file:read"}], "unexpected": 1},
        )
        assert r.status_code == 400

    async def test_empty_capability_names_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": ""}]},
        )
        assert r.status_code == 400
        assert "non-empty name" in r.json()["detail"]

    async def test_agent_not_active_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        host = await host_store.get(sess.host_id)
        assert host is not None
        inactive = sess.model_copy(update={"status": "expired"})

        async def _fake_verify(
            *_args: object,
            **_kwargs: object,
        ) -> tuple[JwtVerifyResult, None]:
            return (
                JwtVerifyResult(ok=True, agent=inactive, host=host, claims={}),
                None,
            )

        monkeypatch.setattr(
            "asap.transport.escalation_routes.verify_agent_bearer",
            _fake_verify,
        )
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "file:read"}]},
        )
        assert r.status_code == 400
        assert "active" in r.json()["detail"]

    async def test_registry_unconfigured_returns_500(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        app.state.capability_registry = None
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "file:read"}]},
        )
        assert r.status_code == 500

    async def test_approval_store_missing_returns_500_when_consent_required(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        app.state.identity_approval_store = None
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "admin:config"}]},
        )
        assert r.status_code == 500

    async def test_ciba_path_when_host_linked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        host = await host_store.get(sess.host_id)
        assert host is not None
        await host_store.save(host.model_copy(update={"user_id": "linked-user-1"}))
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "admin:config"}]},
        )
        assert r.status_code == 200
        assert r.json()["approval"]["method"] == "ciba"

    async def test_a2h_background_task_scheduled(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        ch = AsyncMock()
        app.state.identity_approval_a2h_channel = ch
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "admin:config"}]},
        )
        assert r.status_code == 200
        ch.resolve_via_a2h.assert_awaited_once()
        _call = ch.resolve_via_a2h.await_args
        assert _call is not None
        assert _call.kwargs["principal_id"] == sess.host_id

    async def test_a2h_uses_linked_user_id_as_principal(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """When host.user_id is set, A2H escalation uses user_id as principal_id."""
        caps = [
            CapabilityDefinition(name="file:read", description="r"),
            CapabilityDefinition(name="admin:config", description="a"),
        ]
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=caps
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        client = TestClient(app)
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        ch = AsyncMock()
        app.state.identity_approval_a2h_channel = ch
        await _activate_host_with_defaults(
            host_store, agent_store, aid, default_capabilities=["file:read"]
        )
        sess = await agent_store.get(aid)
        assert sess is not None
        host = await host_store.get(sess.host_id)
        assert host is not None
        await host_store.save(host.model_copy(update={"user_id": "linked-user-42"}))
        registry.grant(aid, "file:read", granted_by=sess.host_id)
        tok = _agent_token(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": f"Bearer {tok}"},
            json={"capabilities": [{"name": "admin:config"}]},
        )
        assert r.status_code == 200
        ch.resolve_via_a2h.assert_awaited_once()
        _call = ch.resolve_via_a2h.await_args
        assert _call is not None
        assert _call.kwargs["principal_id"] == "linked-user-42"

    async def test_verify_returns_incomplete_identity_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caps = [CapabilityDefinition(name="file:read", description="r")]
        app, *_rest = _setup(sample_manifest, isolated_rate_limiter, capabilities=caps)

        async def _fake_verify(
            *_args: object,
            **_kwargs: object,
        ) -> tuple[SimpleNamespace, None]:
            return SimpleNamespace(agent=None, host=SimpleNamespace()), None

        monkeypatch.setattr(
            "asap.transport.escalation_routes.verify_agent_bearer",
            _fake_verify,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/request-capability",
            headers={"Authorization": "Bearer x"},
            json={"capabilities": [{"name": "file:read"}]},
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid agent token"
