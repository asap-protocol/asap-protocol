"""HTTP tests: auto-grant must not replace constrained, denied, or expiring grants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from asap.auth.agent_jwt import create_host_jwt
from asap.auth.capabilities import CapabilityDefinition, CapabilityRegistry
from asap.auth.identity import AgentStore, HostStore
from tests.transport.test_capability_routes import (
    _HOST_JWT_AUDIENCE,
    _register_and_activate,
    _setup,
)
from tests.transport.test_escalation_routes import _activate_host_with_defaults, _agent_token

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_PATH_TMP: dict[str, object] = {"path": {"in": ["/tmp"]}}


async def _prepare_file_read_agent(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
) -> tuple[
    FastAPI,
    TestClient,
    AgentStore,
    HostStore,
    CapabilityRegistry,
    str,
    Ed25519PrivateKey,
    Ed25519PrivateKey,
]:
    """Register an active agent whose host defaults include ``file:read``."""
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
    return app, client, agent_store, host_store, registry, aid, host_sk, agent_sk


def _post_request_capability(
    client: TestClient,
    agent_sk: Ed25519PrivateKey,
    host_sk: Ed25519PrivateKey,
    agent_id: str,
    capabilities: list[dict[str, Any]],
) -> Response:
    tok = _agent_token(agent_sk, host_sk, agent_id)
    return client.post(
        "/asap/agent/request-capability",
        headers={"Authorization": f"Bearer {tok}"},
        json={"capabilities": capabilities},
    )


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestEscalationConstraintOverwrite:
    """Auto-grant must not clear, weaken, deny-flip, or strip expiry on a default grant."""

    async def test_clearing_path_constraint_requires_consent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id, constraints=_PATH_TMP)
        blocked = registry.check_grant(aid, "file:read", {"path": "/etc"})
        assert blocked.allowed is False
        r = _post_request_capability(client, agent_sk, host_sk, aid, [{"name": "file:read"}])
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert "approval" in body
        still_blocked = registry.check_grant(aid, "file:read", {"path": "/etc"})
        assert still_blocked.allowed is False
        grants = registry.get_grants(aid)
        assert len(grants) == 1
        assert grants[0].constraints == _PATH_TMP

    async def test_weaker_in_list_requires_consent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id, constraints=_PATH_TMP)
        r = _post_request_capability(
            client,
            agent_sk,
            host_sk,
            aid,
            [{"name": "file:read", "constraints": {"path": {"in": ["/tmp", "/etc"]}}}],
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        still_blocked = registry.check_grant(aid, "file:read", {"path": "/etc"})
        assert still_blocked.allowed is False

    async def test_identical_constraints_remain_auto_grant(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        first = registry.grant(aid, "file:read", granted_by=sess.host_id, constraints=_PATH_TMP)
        r = _post_request_capability(
            client,
            agent_sk,
            host_sk,
            aid,
            [{"name": "file:read", "constraints": _PATH_TMP}],
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"
        current = registry.get_grants(aid)[0]
        assert current is first
        assert registry.check_grant(aid, "file:read", {"path": "/tmp"}).allowed is True
        assert registry.check_grant(aid, "file:read", {"path": "/etc"}).allowed is False

    async def test_denied_grant_requires_consent_and_stays_denied(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id, status="denied")
        r = _post_request_capability(client, agent_sk, host_sk, aid, [{"name": "file:read"}])
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert registry.check_grant(aid, "file:read", {"path": "/tmp"}).allowed is False
        grants = registry.get_grants(aid)
        assert len(grants) == 1
        assert grants[0].status == "denied"

    async def test_future_expiry_requires_consent_and_keeps_expires_at(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        registry.grant(
            aid,
            "file:read",
            granted_by=sess.host_id,
            constraints=_PATH_TMP,
            expires_at=expires,
        )
        r = _post_request_capability(
            client,
            agent_sk,
            host_sk,
            aid,
            [{"name": "file:read", "constraints": _PATH_TMP}],
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        current = registry.get_grants(aid)[0]
        assert current.expires_at == expires
        assert current.constraints == _PATH_TMP

    async def test_expired_grant_requires_consent_and_is_not_renewed(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            _app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        expired = datetime.now(timezone.utc) - timedelta(minutes=1)
        registry.grant(
            aid,
            "file:read",
            granted_by=sess.host_id,
            expires_at=expired,
        )
        assert registry.check_grant(aid, "file:read", None).allowed is False
        r = _post_request_capability(client, agent_sk, host_sk, aid, [{"name": "file:read"}])
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        current = registry.get_grants(aid)[0]
        assert current.expires_at == expired
        assert registry.check_grant(aid, "file:read", None).allowed is False

    async def test_approved_constraint_clear_allows_etc_path(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id, constraints=_PATH_TMP)
        esc = _post_request_capability(client, agent_sk, host_sk, aid, [{"name": "file:read"}])
        assert esc.status_code == 200
        assert esc.json()["status"] == "pending"
        await app.state.identity_approval_store.approve(aid, "reviewer")
        host_jwt = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {host_jwt}"},
        )
        assert st.status_code == 200
        assert registry.check_grant(aid, "file:read", {"path": "/etc"}).allowed is True

    async def test_a2h_context_includes_requested_constraints(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        (
            app,
            client,
            agent_store,
            _hosts,
            registry,
            aid,
            host_sk,
            agent_sk,
        ) = await _prepare_file_read_agent(sample_manifest, isolated_rate_limiter)
        ch = AsyncMock()
        app.state.identity_approval_a2h_channel = ch
        sess = await agent_store.get(aid)
        assert sess is not None
        registry.grant(aid, "file:read", granted_by=sess.host_id, constraints=_PATH_TMP)
        r = _post_request_capability(client, agent_sk, host_sk, aid, [{"name": "file:read"}])
        assert r.status_code == 200
        ch.resolve_via_a2h.assert_awaited_once()
        call = ch.resolve_via_a2h.await_args
        assert call is not None
        context = str(call.kwargs["context"])
        assert "file:read" in context
        assert "no constraints" in context or "constraints" in context
