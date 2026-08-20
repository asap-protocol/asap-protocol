"""Integration tests for capability and reactivation HTTP endpoints.

Covers:
- ``GET  /asap/capability/list``   — no-auth, Agent JWT, query/cursor/limit
- ``GET  /asap/capability/describe`` — found, not found (404)
- ``POST /asap/capability/execute``  — success, no grant, constraint violation, bad body
- ``POST /asap/agent/reactivate``    — success, revoked, rejected, pending, absolute exceeded, wrong host
- ``POST /asap/agent/register``      — with capabilities (partial approval)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_agent_jwt, create_host_jwt
from asap.auth.capabilities import CapabilityDefinition, CapabilityRegistry
from asap.auth.identity import (
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _host_jwt(
    host_sk: Ed25519PrivateKey,
    *,
    agent_public_key: dict[str, Any] | None = None,
) -> str:
    return create_host_jwt(
        host_sk,
        aud=_HOST_JWT_AUDIENCE,
        agent_public_key=agent_public_key,
        ttl_seconds=120,
    )


def _setup(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    *,
    capabilities: list[CapabilityDefinition] | None = None,
) -> tuple[FastAPI, InMemoryAgentStore, InMemoryHostStore, CapabilityRegistry]:
    """Create app with identity stores and a capability registry."""
    agent_store = InMemoryAgentStore()
    host_store = InMemoryHostStore(agent_store=agent_store)
    registry = CapabilityRegistry()
    for cap in capabilities or []:
        registry.register(cap)

    app = create_app(
        sample_manifest,
        rate_limit="999999/minute",
        identity_host_store=host_store,
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    app.state.capability_registry = registry
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    return app, agent_store, host_store, registry


async def _register_and_activate(
    client: TestClient,
    app: FastAPI,
    agent_store: InMemoryAgentStore,
    host_sk: Ed25519PrivateKey,
    agent_sk: Ed25519PrivateKey,
    *,
    status: str = "active",
    session_ttl: timedelta | None = None,
    max_lifetime: timedelta | None = None,
    absolute_lifetime: timedelta | None = None,
    activated_at: datetime | None = None,
) -> str:
    """Register an agent, activate it in the store, and return agent_id."""
    agent_pub = ed25519_public_jwk(agent_sk)
    token = _host_jwt(host_sk, agent_public_key=agent_pub)
    r = client.post("/asap/agent/register", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    reg = r.json()
    aid = reg["agent_id"]
    if reg.get("status") == "pending":
        approval_store = app.state.identity_approval_store
        await approval_store.approve(aid, "test-operator")
        poll = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {_host_jwt(host_sk)}"},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "active"

    sess = await agent_store.get(aid)
    assert sess is not None
    now = datetime.now(timezone.utc)
    updates: dict[str, Any] = {"status": status}
    if activated_at is not None:
        updates["activated_at"] = activated_at
    elif status == "active":
        updates["activated_at"] = now
    if session_ttl is not None:
        updates["session_ttl"] = session_ttl
    if max_lifetime is not None:
        updates["max_lifetime"] = max_lifetime
    if absolute_lifetime is not None:
        updates["absolute_lifetime"] = absolute_lifetime
    updates["last_used_at"] = now
    await agent_store.save(sess.model_copy(update=updates))
    return aid


def _agent_jwt(
    agent_sk: Ed25519PrivateKey,
    host_sk: Ed25519PrivateKey,
    agent_id: str,
) -> str:
    host_tp = jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
    return create_agent_jwt(
        agent_sk,
        host_thumbprint=host_tp,
        agent_id=agent_id,
        aud=_HOST_JWT_AUDIENCE,
    )


_DEFAULT_CAPS = [
    CapabilityDefinition(name="file:read", description="Read a file"),
    CapabilityDefinition(
        name="file:write",
        description="Write a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    CapabilityDefinition(name="admin:config", description="Admin configuration"),
]


# ---------------------------------------------------------------------------
# GET /asap/capability/list
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestCapabilityList:
    def test_list_unauthenticated(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/list")
        assert r.status_code == 200
        body = r.json()
        assert len(body["capabilities"]) == 3
        # No grant_status when unauthenticated
        assert "grant_status" not in body["capabilities"][0]

    def test_list_with_query_filter(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/list?query=file")
        assert r.status_code == 200
        caps = r.json()["capabilities"]
        assert len(caps) == 2
        assert all("file" in c["name"] for c in caps)

    def test_list_pagination(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/list?cursor=0&limit=2")
        body = r.json()
        assert len(body["capabilities"]) == 2
        assert body["next_cursor"] == 2

        r2 = TestClient(app).get("/asap/capability/list?cursor=2&limit=2")
        body2 = r2.json()
        assert len(body2["capabilities"]) == 1
        assert "next_cursor" not in body2

    def test_list_invalid_cursor_returns_422(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Non-integer cursor is caught by FastAPI validation (not a raw 500)."""
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/list?cursor=abc")
        assert r.status_code == 422

    def test_list_invalid_limit_returns_422(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/list?limit=-1")
        assert r.status_code == 422

    async def test_list_with_agent_jwt_includes_grant_status(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)

        registry.grant(aid, "file:read")
        registry.grant(aid, "file:write", status="denied", reason="policy")

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.get(
            "/asap/capability/list",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        caps = r.json()["capabilities"]
        by_name = {c["name"]: c for c in caps}
        assert by_name["file:read"]["grant_status"] == "active"
        assert by_name["file:write"]["grant_status"] == "denied"
        assert by_name["admin:config"]["grant_status"] is None


# ---------------------------------------------------------------------------
# GET /asap/capability/describe
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestCapabilityDescribe:
    def test_describe_found(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/describe?name=file:write")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "file:write"
        assert "input_schema" in body

    def test_describe_not_found(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/describe?name=nonexistent")
        assert r.status_code == 404

    def test_describe_missing_name_returns_422(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).get("/asap/capability/describe")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /asap/capability/execute
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestCapabilityExecute:
    async def test_execute_success(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        registry.grant(aid, "file:read")

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "file:read"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "executed"

    async def test_execute_no_grant_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, _ = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "file:read"},
        )
        assert r.status_code == 403
        body = r.json()
        err = body["error"]
        assert err["code"] == "capability_not_granted"
        assert err["data"]["required_capability"] == "file:read"
        assert "request_id" in err and err["request_id"]

    async def test_execute_agent_expired_by_max_lifetime_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        aid = await _register_and_activate(
            client,
            app,
            agent_store,
            host_sk,
            agent_sk,
            activated_at=past,
            max_lifetime=timedelta(hours=1),
        )
        registry.grant(aid, "file:read")

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "file:read"},
        )
        assert r.status_code == 403
        assert "expired" in r.json()["detail"].lower()
        assert "request_id" in r.json() and r.json()["request_id"]

    async def test_execute_revoked_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """A revoked HOST must not authenticate on capability execute.

        Mirrors ``test_reactivate_revoked_host_returns_403``: the Host-JWT
        verifier must reject revoked hosts on every capability route, not only
        ``/asap/agent/reactivate``.
        """
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="active"
        )
        registry.grant(aid, "file:read")

        host = await host_store.get_by_public_key(
            jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
        )
        assert host is not None
        # Mark host revoked without ``HostStore.revoke()`` so agent rows stay
        # active and we exercise the host-revoked branch in ``verify_agent_jwt``.
        await host_store.save(
            host.model_copy(
                update={
                    "status": "revoked",
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "file:read"},
        )
        assert r.status_code == 403
        assert "revoked" in r.json()["detail"].lower()

    async def test_execute_constraint_violated_returns_403_with_violations(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        registry.grant(aid, "file:read", constraints={"path": {"in": ["/tmp"]}})

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "file:read", "arguments": {"path": "/etc/passwd"}},
        )
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "constraint_violated"
        assert len(body["violations"]) == 1
        assert body["violations"][0]["field"] == "path"
        assert body["violations"][0]["operator"] == "in"
        assert "request_id" in body and body["request_id"]

    async def test_execute_no_auth_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS)
        r = TestClient(app).post(
            "/asap/capability/execute",
            json={"capability": "file:read"},
        )
        assert r.status_code == 401
        www = r.headers.get("www-authenticate", "")
        assert "Bearer" in www
        assert "ASAP" in www

    async def test_execute_invalid_json_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        registry.grant(aid, "file:read")

        token = _agent_jwt(agent_sk, host_sk, aid)
        r = client.post(
            "/asap/capability/execute",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            content=b"not json",
        )
        assert r.status_code == 400

    async def test_execute_invalid_body_returns_400_with_detail(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Pydantic validation errors are returned, not swallowed."""
        app, agent_store, _, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
        registry.grant(aid, "file:read")

        token = _agent_jwt(agent_sk, host_sk, aid)
        # Missing required 'capability' field
        r = client.post(
            "/asap/capability/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"arguments": {"path": "/tmp"}},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        # Should contain Pydantic validation error detail, not generic message
        assert isinstance(detail, list)
        assert any("capability" in str(e) for e in detail)


# ---------------------------------------------------------------------------
# POST /asap/agent/reactivate
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentReactivate:
    async def test_reactivate_success(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="expired"
        )
        registry.grant(aid, "file:read")
        registry.grant(aid, "admin:config")

        # Set host default capabilities
        host = await host_store.get_by_public_key(
            jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
        )
        assert host is not None
        updated_host = host.model_copy(
            update={
                "default_capabilities": ["file:read"],
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await host_store.save(updated_host)

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "active"
        # Capability decay: admin:config should be denied, file:read re-granted
        caps = {c["capability"]: c["status"] for c in body["capabilities"]}
        assert caps["file:read"] == "active"
        assert caps["admin:config"] == "denied"

    async def test_reactivate_revoked_agent_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="revoked"
        )

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "revoked" in r.json()["detail"].lower()

    async def test_reactivate_rejected_agent_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Denied registration must not be flipped to active via reactivate."""
        app, agent_store, _, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="rejected"
        )

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "rejected" in r.json()["detail"].lower()
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "rejected"

    async def test_reactivate_pending_agent_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Pending approval must not be skipped by POST /asap/agent/reactivate."""
        app, agent_store, _, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="pending"
        )

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "pending" in r.json()["detail"].lower()
        stored = await agent_store.get(aid)
        assert stored is not None and stored.status == "pending"

    async def test_reactivate_revoked_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """A revoked HOST must not authenticate on capability routes.

        Distinct from ``test_reactivate_revoked_agent_returns_403`` (which
        revokes the AGENT and hits the agent-status check inside
        ``reactivate_agent``): here the HOST is revoked, so the Host-JWT
        verifier itself must reject the request with 403.
        """
        app, agent_store, host_store, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="active"
        )

        host = await host_store.get_by_public_key(
            jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
        )
        assert host is not None
        await host_store.revoke(host.host_id)

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "revoked" in r.json()["detail"].lower()

    async def test_reactivate_absolute_lifetime_exceeded_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, _, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()

        now = datetime.now(timezone.utc)
        aid = await _register_and_activate(
            client,
            app,
            agent_store,
            host_sk,
            agent_sk,
            status="expired",
            absolute_lifetime=timedelta(days=1),
        )
        # Move created_at back beyond the absolute lifetime
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"created_at": now - timedelta(days=2)}))

        token = _host_jwt(host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "absolute lifetime" in r.json()["detail"].lower()

    async def test_reactivate_wrong_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, agent_store, host_store, _ = _setup(sample_manifest, isolated_rate_limiter)
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        other_host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        aid = await _register_and_activate(
            client, app, agent_store, host_sk, agent_sk, status="expired"
        )

        # Register the other host so lookup by iss succeeds
        other_agent_sk = Ed25519PrivateKey.generate()
        other_agent_pub = ed25519_public_jwk(other_agent_sk)
        other_reg_token = _host_jwt(other_host_sk, agent_public_key=other_agent_pub)
        r_reg = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {other_reg_token}"},
        )
        assert r_reg.status_code == 200

        token = _host_jwt(other_host_sk)
        r = client.post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403
        assert "does not belong" in r.json()["detail"]

    def test_reactivate_no_auth_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter)
        r = TestClient(app).post(
            "/asap/agent/reactivate",
            json={"agent_id": "any"},
        )
        assert r.status_code == 401

    def test_reactivate_unknown_agent_returns_404(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, *_ = _setup(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        token = _host_jwt(host_sk)
        r = TestClient(app).post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_id": "nonexistent"},
        )
        assert r.status_code == 404

    def test_reactivate_invalid_body_returns_400_with_detail(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        """Pydantic validation errors are returned, not swallowed."""
        app, *_ = _setup(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        token = _host_jwt(host_sk)
        r = TestClient(app).post(
            "/asap/agent/reactivate",
            headers={"Authorization": f"Bearer {token}"},
            json={"wrong_field": "value"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert isinstance(detail, list)


# ---------------------------------------------------------------------------
# POST /asap/agent/register with capabilities
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestRegisterWithCapabilities:
    async def test_register_with_known_capabilities_grants_active(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, _, host_store, _registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        host_pub = ed25519_public_jwk(host_sk)
        agent_pub = ed25519_public_jwk(agent_sk)
        now = datetime.now(timezone.utc)
        await host_store.save(
            HostIdentity(
                host_id=host_urn_from_thumbprint(jwk_thumbprint_sha256(host_pub)),
                public_key=dict(host_pub),
                status="active",
                default_capabilities=["file:read", "file:write", "admin:config"],
                created_at=now,
                updated_at=now,
            )
        )
        token = _host_jwt(host_sk, agent_public_key=agent_pub)

        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"capabilities": ["file:read", "file:write"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "active"
        grants = body.get("agent_capability_grants", [])
        assert len(grants) == 2
        assert all(g["status"] == "active" for g in grants)

    async def test_register_with_unknown_capability_denied(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, _agent_store, _host_store, registry = _setup(
            sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
        )
        client = TestClient(app)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_pub = ed25519_public_jwk(agent_sk)
        token = _host_jwt(host_sk, agent_public_key=agent_pub)

        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"capabilities": ["file:read", "nonexistent:cap"]},
        )
        assert r.status_code == 200
        reg = r.json()
        assert reg["status"] == "pending"
        aid = reg["agent_id"]
        await app.state.identity_approval_store.approve(aid, "test-user")
        poll = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {_host_jwt(host_sk)}"},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "active"

        by_cap = {g.capability: g for g in registry.get_grants(aid)}
        assert by_cap["file:read"].status == "active"
        assert by_cap["nonexistent:cap"].status == "denied"
        assert by_cap["nonexistent:cap"].reason


# ---------------------------------------------------------------------------
# Parity: missing identity_limiter surfaces a clean 503, not an AttributeError/500
# (S2 #240 must-fix 1 — HTTP-side identity-route migration).
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestIdentityLimiterMissingReturns503:
    """Identity routes must 503 cleanly when ``identity_limiter`` is unconfigured.

    Previously the inline ``request.app.state.identity_limiter.check(request)``
    raised ``AttributeError`` (surfacing as HTTP 500) when the limiter was not
    wired. The ``Depends(require_identity_limiter)`` migration converts that into
    a typed 503.
    """

    async def test_capability_list_returns_503_when_identity_limiter_missing(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: ASAPRateLimiter | None,
    ) -> None:
        app, _agent_store, _host_store, _registry = _setup(sample_manifest, isolated_rate_limiter)
        # Simulate a misconfigured server: identity routes mounted but no limiter.
        if hasattr(app.state, "identity_limiter"):
            delattr(app.state, "identity_limiter")
        client = TestClient(app)

        r = client.get("/asap/capability/list")

        assert r.status_code == 503
        assert "identity_limiter not set" in r.json()["detail"]
