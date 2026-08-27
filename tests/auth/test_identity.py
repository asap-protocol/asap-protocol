"""Tests for per-runtime-agent identity models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from asap.auth.identity import (
    AgentMode,
    AgentSession,
    AgentSessionStatus,
    AgentStore,
    HostIdentity,
    HostStatus,
    HostStore,
    InMemoryAgentStore,
    InMemoryHostStore,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
)
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Reusable valid key for tests that don't need unique keys
_VALID_JWK: dict[str, str] = make_ed25519_jwk()


def test_host_identity_minimal() -> None:
    """HostIdentity accepts required fields and optional defaults."""
    now = _utc_now()
    host = HostIdentity(
        host_id="host-1",
        public_key=_VALID_JWK,
        status="active",
        created_at=now,
        updated_at=now,
    )
    assert host.host_id == "host-1"
    assert host.name is None
    assert host.user_id is None
    assert host.default_capabilities == []
    assert host.status == "active"


def test_agent_session_minimal() -> None:
    """AgentSession accepts required fields and optional lifetimes."""
    now = _utc_now()
    session = AgentSession(
        agent_id="agent-1",
        host_id="host-1",
        public_key=_VALID_JWK,
        mode="delegated",
        status="pending",
        created_at=now,
    )
    assert session.session_ttl is None
    assert session.activated_at is None
    assert session.last_used_at is None


def test_host_identity_all_status_literals_accepted() -> None:
    """HostIdentity accepts each allowed status value."""
    now = _utc_now()
    pk: dict[str, str] = make_ed25519_jwk()
    for status in get_args(HostStatus):
        h = HostIdentity(
            host_id="h",
            public_key=pk,
            status=status,
            created_at=now,
            updated_at=now,
        )
        assert h.status == status


def test_agent_session_all_mode_and_status_literals_accepted() -> None:
    """AgentSession accepts each allowed mode and status value."""
    now = _utc_now()
    pk: dict[str, str] = make_ed25519_jwk()
    for mode in get_args(AgentMode):
        for status in get_args(AgentSessionStatus):
            s = AgentSession(
                agent_id="a",
                host_id="h",
                public_key=pk,
                mode=mode,
                status=status,
                created_at=now,
            )
            assert s.mode == mode
            assert s.status == status


def test_host_identity_invalid_status_rejected() -> None:
    """Invalid host status string fails validation."""
    now = _utc_now()
    with pytest.raises(ValidationError):
        HostIdentity.model_validate(
            {
                "host_id": "h",
                "public_key": _VALID_JWK,
                "status": "unknown",
                "created_at": now,
                "updated_at": now,
            }
        )


def test_agent_session_invalid_mode_or_status_rejected() -> None:
    """Invalid agent mode or status fails validation."""
    now = _utc_now()
    base: dict[str, Any] = {
        "agent_id": "a",
        "host_id": "h",
        "public_key": _VALID_JWK,
        "created_at": now,
    }
    with pytest.raises(ValidationError):
        AgentSession.model_validate({**base, "mode": "hybrid", "status": "active"})
    with pytest.raises(ValidationError):
        AgentSession.model_validate({**base, "mode": "delegated", "status": "deleted"})


def test_host_identity_optional_fields_round_trip() -> None:
    """name, user_id, and default_capabilities are preserved."""
    now = _utc_now()
    host = HostIdentity(
        host_id="h",
        name="My Host",
        public_key=_VALID_JWK,
        user_id="user-9",
        default_capabilities=["asap:execute", "asap:read"],
        status="pending",
        created_at=now,
        updated_at=now,
    )
    assert host.name == "My Host"
    assert host.user_id == "user-9"
    assert host.default_capabilities == ["asap:execute", "asap:read"]


def test_models_are_frozen() -> None:
    """Identity models reject in-place mutation (ASAPBaseModel frozen)."""
    now = _utc_now()
    host = HostIdentity(
        host_id="h",
        public_key=_VALID_JWK,
        status="active",
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValidationError, match="frozen"):
        host.host_id = "other"


def test_models_reject_extra_fields() -> None:
    """Unknown fields are rejected (extra=forbid via ASAPBaseModel)."""
    now = _utc_now()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HostIdentity(
            host_id="h",
            public_key=_VALID_JWK,
            status="active",
            created_at=now,
            updated_at=now,
            not_a_field="no",
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentSession(
            agent_id="a",
            host_id="h",
            public_key=_VALID_JWK,
            mode="autonomous",
            status="active",
            created_at=now,
            unexpected=True,
        )


def test_agent_session_optional_timedeltas() -> None:
    """Optional lifetime fields round-trip on AgentSession."""
    now = _utc_now()
    session = AgentSession(
        agent_id="a",
        host_id="h",
        public_key=_VALID_JWK,
        mode="delegated",
        status="active",
        session_ttl=timedelta(seconds=60),
        max_lifetime=timedelta(hours=1),
        absolute_lifetime=timedelta(days=1),
        activated_at=now,
        last_used_at=now,
        created_at=now,
    )
    assert session.session_ttl == timedelta(seconds=60)
    assert session.max_lifetime == timedelta(hours=1)


class _StubHostStore:
    """Minimal async implementation for runtime protocol checks."""

    async def save(self, host: HostIdentity) -> None:
        return None

    async def get(self, host_id: str) -> HostIdentity | None:
        return None

    async def get_by_public_key(self, thumbprint: str) -> HostIdentity | None:
        return None

    async def revoke(self, host_id: str) -> None:
        return None


class _StubAgentStore:
    """Minimal async implementation for runtime protocol checks."""

    async def save(self, agent: AgentSession) -> None:
        return None

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict[str, Any],
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        return None

    async def get(self, agent_id: str) -> AgentSession | None:
        return None

    async def list_by_host(self, host_id: str) -> list[AgentSession]:
        return []

    async def revoke(self, agent_id: str) -> None:
        return None

    async def revoke_by_host(self, host_id: str) -> None:
        return None


class _IncompleteHostStore:
    """Missing protocol methods — should fail runtime isinstance."""

    async def save(self, host: HostIdentity) -> None:
        return None


class _IncompleteAgentStore:
    """Missing protocol methods — should fail runtime isinstance."""

    async def save(self, agent: AgentSession) -> None:
        return None


def test_host_store_protocol_runtime_checkable() -> None:
    """HostStore is @runtime_checkable and recognizes conforming implementations."""
    assert isinstance(_StubHostStore(), HostStore)
    assert not isinstance(_IncompleteHostStore(), HostStore)


def test_agent_store_protocol_runtime_checkable() -> None:
    """AgentStore is @runtime_checkable and recognizes conforming implementations."""
    assert isinstance(_StubAgentStore(), AgentStore)
    assert not isinstance(_IncompleteAgentStore(), AgentStore)


def test_in_memory_stores_satisfy_protocols() -> None:
    """Concrete in-memory stores are recognized as protocol implementations."""
    assert isinstance(InMemoryHostStore(), HostStore)
    assert isinstance(InMemoryAgentStore(), AgentStore)


async def test_in_memory_host_store_round_trip_and_thumbprint() -> None:
    """Host save/get and lookup by RFC 7638 thumbprint."""
    store = InMemoryHostStore()
    now = _utc_now()
    public_key = make_ed25519_jwk()
    host = HostIdentity(
        host_id="host-1",
        public_key=public_key,
        status="active",
        created_at=now,
        updated_at=now,
    )
    await store.save(host)
    assert await store.get("host-1") == host
    tp = jwk_thumbprint_sha256(public_key)
    assert await store.get_by_public_key(tp) == host


async def test_in_memory_host_public_key_rotation_updates_thumb_index() -> None:
    """Replacing a host public key removes the old thumbprint mapping."""
    store = InMemoryHostStore()
    now = _utc_now()
    v1 = HostIdentity(
        host_id="hid",
        public_key=make_ed25519_jwk(),
        status="active",
        created_at=now,
        updated_at=now,
    )
    await store.save(v1)
    tp1 = jwk_thumbprint_sha256(v1.public_key)
    v2 = v1.model_copy(
        update={
            "public_key": make_ed25519_jwk(),
            "updated_at": now,
        }
    )
    await store.save(v2)
    assert await store.get_by_public_key(tp1) is None
    assert await store.get_by_public_key(jwk_thumbprint_sha256(v2.public_key)) == v2


async def test_in_memory_host_revoke_cascades_to_agents() -> None:
    """Host revocation with linked agent store revokes all agents for that host."""
    agents = InMemoryAgentStore()
    hosts = InMemoryHostStore(agent_store=agents)
    now = _utc_now()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=make_ed25519_jwk(),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await agents.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=make_ed25519_jwk(),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    await hosts.revoke("h1")
    host = await hosts.get("h1")
    assert host is not None and host.status == "revoked"
    agent = await agents.get("a1")
    assert agent is not None and agent.status == "revoked"


async def test_in_memory_agent_store_list_and_revoke() -> None:
    """Agent list_by_host, revoke, and revoke_by_host."""
    store = InMemoryAgentStore()
    now = _utc_now()
    a1 = AgentSession(
        agent_id="a1",
        host_id="h1",
        public_key=make_ed25519_jwk(),
        mode="delegated",
        status="active",
        created_at=now,
    )
    a2 = AgentSession(
        agent_id="a2",
        host_id="h1",
        public_key=make_ed25519_jwk(),
        mode="autonomous",
        status="pending",
        created_at=now,
    )
    await store.save(a1)
    await store.save(a2)
    listed = await store.list_by_host("h1")
    assert [s.agent_id for s in listed] == ["a1", "a2"]
    await store.revoke("a1")
    updated = await store.get("a1")
    assert updated is not None and updated.status == "revoked"
    await store.revoke_by_host("h1")
    a2_after = await store.get("a2")
    assert a2_after is not None and a2_after.status == "revoked"


async def test_touch_if_current_slides_last_used_at_without_clobbering_key() -> None:
    """Atomic touch updates only last_used_at when status, host, and key match."""
    store = InMemoryAgentStore()
    now = _utc_now()
    public_key = make_ed25519_jwk()
    await store.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=public_key,
            mode="delegated",
            status="active",
            created_at=now,
            last_used_at=now - timedelta(minutes=5),
        )
    )
    touched_at = now + timedelta(seconds=1)
    updated = await store.touch_if_current(
        "a1",
        {**public_key, "kid": "optional-member"},
        touched_at,
        expected_host_id="h1",
    )
    assert updated is not None
    assert updated.last_used_at == touched_at
    assert updated.status == "active"
    assert updated.public_key == public_key
    stored = await store.get("a1")
    assert stored == updated


async def test_touch_if_current_refuses_revoked_rotated_or_rehosted_row() -> None:
    """Predicate fails closed so a later save cannot resurrect or undo rotate."""
    store = InMemoryAgentStore()
    now = _utc_now()
    public_key = make_ed25519_jwk()
    session = AgentSession(
        agent_id="a1",
        host_id="h1",
        public_key=public_key,
        mode="delegated",
        status="active",
        created_at=now,
    )
    await store.save(session)
    await store.revoke("a1")
    assert (await store.touch_if_current("a1", public_key, now, expected_host_id="h1")) is None
    revoked = await store.get("a1")
    assert revoked is not None and revoked.status == "revoked"

    await store.save(session)
    rotated = make_ed25519_jwk()
    await store.save(session.model_copy(update={"public_key": rotated}))
    assert (await store.touch_if_current("a1", public_key, now, expected_host_id="h1")) is None
    kept = await store.get("a1")
    assert kept is not None and kept.public_key == rotated

    await store.save(session)
    await store.save(session.model_copy(update={"host_id": "other-host"}))
    assert (await store.touch_if_current("a1", public_key, now, expected_host_id="h1")) is None
    rehosted = await store.get("a1")
    assert rehosted is not None and rehosted.host_id == "other-host"


def test_jwk_thumbprint_sha256_is_deterministic() -> None:
    """Same JWK dict always yields the same thumbprint."""
    jwk_dict = {"crv": "Ed25519", "kty": "OKP", "x": "dGVzdA"}
    assert jwk_thumbprint_sha256(jwk_dict) == jwk_thumbprint_sha256(dict(jwk_dict))


def test_jwk_thumbprint_sha256_ignores_optional_jwk_fields() -> None:
    """RFC 7638: thumbprint uses only required members; ``kid``/``use`` do not change it."""
    minimal = {"kty": "OKP", "crv": "Ed25519", "x": "dGVzdA"}
    with_extras = {**minimal, "kid": "key-1", "use": "sig"}
    assert jwk_thumbprint_sha256(minimal) == jwk_thumbprint_sha256(with_extras)


def test_host_urn_from_thumbprint() -> None:
    """Synthetic host id is stable for a given thumbprint string."""
    tp = "AbCdEf"
    assert host_urn_from_thumbprint(tp) == "urn:asap:host:AbCdEf"


def test_jwk_thumbprint_sha256_unsupported_kty_raises() -> None:
    """Unsupported ``kty`` values raise ``ValueError``."""
    with pytest.raises(ValueError, match="unsupported kty"):
        jwk_thumbprint_sha256({"kty": "bogus", "x": "x"})


async def test_in_memory_host_store_get_and_revoke_edge_cases() -> None:
    """Missing ids return None; revoke is idempotent; cascade optional."""
    store = InMemoryHostStore()
    assert await store.get("missing") is None
    assert await store.get_by_public_key("nope") is None
    await store.revoke("missing")

    now = _utc_now()
    await store.save(
        HostIdentity(
            host_id="h1",
            public_key=make_ed25519_jwk(),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await store.revoke("h1")
    twice = await store.get("h1")
    assert twice is not None and twice.status == "revoked"
    await store.revoke("h1")
    final = await store.get("h1")
    assert final is not None and final.status == "revoked"


async def test_in_memory_agent_store_get_and_revoke_edge_cases() -> None:
    """Missing agent returns None; revoke idempotent."""
    store = InMemoryAgentStore()
    assert await store.get("nope") is None
    await store.revoke("nope")
    assert await store.list_by_host("empty") == []

    now = _utc_now()
    await store.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=make_ed25519_jwk(),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    await store.revoke("a1")
    await store.revoke("a1")
    final = await store.get("a1")
    assert final is not None and final.status == "revoked"


async def test_host_revoke_cascades_multiple_agents() -> None:
    """Revoking a host revokes every agent session for that host."""
    agents = InMemoryAgentStore()
    hosts = InMemoryHostStore(agent_store=agents)
    now = _utc_now()
    await hosts.save(
        HostIdentity(
            host_id="hx",
            public_key=make_ed25519_jwk(),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    for aid in ("a1", "a2", "a3"):
        await agents.save(
            AgentSession(
                agent_id=aid,
                host_id="hx",
                public_key=make_ed25519_jwk(),
                mode="delegated",
                status="active",
                created_at=now,
            )
        )
    await hosts.revoke("hx")
    for aid in ("a1", "a2", "a3"):
        row = await agents.get(aid)
        assert row is not None and row.status == "revoked"
