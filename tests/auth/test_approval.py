"""Tests for registration approval flows (Device Authorization, CIBA, A2H channel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from asap.auth.approval import (
    A2HApprovalChannel,
    ApprovalObject,
    InMemoryApprovalStore,
    USER_CODE_ALPHABET,
    USER_CODE_LENGTH,
    check_approval_status,
    create_ciba_approval,
    create_device_authorization,
    select_approval_method,
)
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    HostStatus,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
)
from asap.handlers.hitl import ApprovalDecision, ApprovalResult
from asap.transport.agent_routes import background_a2h_resolve
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _sample_host(
    *,
    user_id: str | None = None,
    status: HostStatus = "pending",
    default_capabilities: list[str] | None = None,
) -> HostIdentity:
    now = datetime.now(timezone.utc)
    pk = make_ed25519_jwk()
    hid = host_urn_from_thumbprint(jwk_thumbprint_sha256(pk))
    return HostIdentity(
        host_id=hid,
        public_key=pk,
        user_id=user_id,
        status=status,
        default_capabilities=default_capabilities or [],
        created_at=now,
        updated_at=now,
    )


def _sample_agent() -> AgentSession:
    now = datetime.now(timezone.utc)
    pk = make_ed25519_jwk()
    return AgentSession(
        agent_id="agent-x",
        host_id="host-y",
        public_key=pk,
        mode="delegated",
        status="pending",
        created_at=now,
    )


class _StubHumanApproval:
    """Minimal async human approval backend for A2H channel tests."""

    def __init__(self, result: ApprovalResult) -> None:
        self._result = result

    async def request_approval(
        self,
        *,
        context: str,
        principal_id: str,
        assurance_level: str = "LOW",
        timeout_seconds: float = 300.0,
    ) -> ApprovalResult:
        _ = (context, principal_id, assurance_level, timeout_seconds)
        return self._result


def test_approval_object_validates_device_and_ciba_shapes() -> None:
    d = ApprovalObject(
        method="device_authorization",
        verification_uri="https://ex.example/device",
        verification_uri_complete="https://ex.example/device?u=1",
        user_code="A" * USER_CODE_LENGTH,
        expires_in=100,
        interval=5,
    )
    assert d.method == "device_authorization"
    c = ApprovalObject(
        method="ciba",
        binding_message="confirm",
        expires_in=200,
        interval=7,
    )
    assert c.verification_uri is None


@pytest.mark.asyncio
async def test_device_flow_poll_approve_and_deny() -> None:
    store = InMemoryApprovalStore()
    obj = await create_device_authorization(store, "ag-1", ["read"])
    assert await check_approval_status(store, "ag-1") == "pending"
    assert len(obj.user_code or "") == USER_CODE_LENGTH
    assert all(ch in USER_CODE_ALPHABET for ch in (obj.user_code or ""))

    await store.approve("ag-1", "user-1")
    assert await check_approval_status(store, "ag-1") == "approved"

    store2 = InMemoryApprovalStore()
    await create_device_authorization(store2, "ag-2", [])
    await store2.deny("ag-2", "no trust")
    assert await check_approval_status(store2, "ag-2") == "denied"


@pytest.mark.asyncio
async def test_ciba_flow_create_then_approve() -> None:
    store = InMemoryApprovalStore()
    obj = await create_ciba_approval(
        store,
        "ag-c",
        ["read"],
        binding_message="Custom binding",
    )
    assert obj.method == "ciba"
    assert obj.binding_message == "Custom binding"
    assert obj.user_code is None
    assert await check_approval_status(store, "ag-c") == "pending"
    await store.approve("ag-c", "op-1")
    assert await check_approval_status(store, "ag-c") == "approved"


@pytest.mark.asyncio
async def test_idempotent_pending_reregistration() -> None:
    store = InMemoryApprovalStore()
    a = await create_device_authorization(store, "r1", ["a"])
    b = await create_device_authorization(store, "r1", ["a"])
    assert a.user_code == b.user_code


@pytest.mark.asyncio
async def test_approval_object_expires_in_reflects_remaining_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryApprovalStore()
    t0 = datetime(2022, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("asap.auth.approval._utcnow", lambda: t0)
    first = await create_device_authorization(store, "ttl-1", ["a"], expires_in=600)
    assert first.expires_in == 600
    monkeypatch.setattr(
        "asap.auth.approval._utcnow",
        lambda: t0 + timedelta(seconds=100),
    )
    second = await create_device_authorization(store, "ttl-1", ["a"], expires_in=600)
    assert second.user_code == first.user_code
    assert second.expires_in == 500


@pytest.mark.asyncio
async def test_background_a2h_resolve_swallows_provider_errors() -> None:
    ch = MagicMock(spec=A2HApprovalChannel)
    ch.resolve_via_a2h = AsyncMock(side_effect=RuntimeError("a2h unavailable"))
    await background_a2h_resolve(
        ch,
        "agent-z",
        context="ctx",
        principal_id="principal-z",
    )
    ch.resolve_via_a2h.assert_awaited_once()


def test_select_linked_host_prefers_ciba_when_supported() -> None:
    h = _sample_host(user_id="u1", status="active")
    assert select_approval_method(h, _sample_agent(), host_supports_ciba=True) == "ciba"


def test_select_unlinked_host_uses_device_authorization() -> None:
    h = _sample_host(user_id=None, status="active")
    assert (
        select_approval_method(h, _sample_agent(), host_supports_ciba=True)
        == "device_authorization"
    )


def test_select_preferred_device_forces_device() -> None:
    h = _sample_host(user_id="u1", status="active")
    assert (
        select_approval_method(
            h,
            _sample_agent(),
            host_supports_ciba=True,
            preferred_method="device_authorization",
        )
        == "device_authorization"
    )


def test_select_preferred_ciba_falls_back_when_unlinked() -> None:
    h = _sample_host(user_id=None, status="active")
    assert (
        select_approval_method(
            h,
            _sample_agent(),
            host_supports_ciba=True,
            preferred_method="ciba",
        )
        == "device_authorization"
    )


def test_select_preferred_ciba_when_linked_and_supported() -> None:
    h = _sample_host(user_id="u1", status="active")
    assert (
        select_approval_method(
            h,
            _sample_agent(),
            host_supports_ciba=True,
            preferred_method="ciba",
        )
        == "ciba"
    )


@pytest.mark.asyncio
async def test_ciba_idempotent_pending_reregistration() -> None:
    store = InMemoryApprovalStore()
    first = await create_ciba_approval(store, "ciba-r1", ["read"])
    second = await create_ciba_approval(store, "ciba-r1", ["read"])
    assert first.method == "ciba"
    assert second.method == "ciba"
    assert first.binding_message == second.binding_message


@pytest.mark.asyncio
async def test_escalation_does_not_reuse_registration_pending() -> None:
    store = InMemoryApprovalStore()
    reg = await create_device_authorization(store, "esc-1", ["read"])
    esc = await create_device_authorization(
        store,
        "esc-1",
        ["read"],
        approval_kind="escalation",
    )
    assert reg.user_code != esc.user_code
    assert await check_approval_status(store, "esc-1") == "pending"


@pytest.mark.asyncio
async def test_in_memory_approval_store_remove_clears_record() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(store, "rm-1", ["read"])
    await store.remove("rm-1")
    assert await store.get("rm-1") is None


@pytest.mark.asyncio
async def test_check_approval_status_raises_when_missing() -> None:
    store = InMemoryApprovalStore()
    with pytest.raises(KeyError, match="No approval request"):
        await check_approval_status(store, "missing-agent")


@pytest.mark.asyncio
async def test_pending_expired_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryApprovalStore()
    frozen = datetime(2022, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("asap.auth.approval._utcnow", lambda: frozen)
    await create_device_authorization(store, "ex-1", [], expires_in=30)
    monkeypatch.setattr(
        "asap.auth.approval._utcnow",
        lambda: frozen + timedelta(seconds=60),
    )
    assert await check_approval_status(store, "ex-1") == "expired"


@pytest.mark.asyncio
async def test_a2h_channel_approve_updates_store() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(store, "a2h-1", ["read"])
    provider = _StubHumanApproval(ApprovalResult(decision=ApprovalDecision.APPROVE))
    ch = A2HApprovalChannel(provider, store)
    await ch.resolve_via_a2h(
        "a2h-1",
        context="register",
        principal_id="human-1",
        timeout_seconds=1.0,
    )
    assert await check_approval_status(store, "a2h-1") == "approved"


@pytest.mark.asyncio
async def test_a2h_channel_decline_updates_store() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(store, "a2h-2", [])
    provider = _StubHumanApproval(
        ApprovalResult(
            decision=ApprovalDecision.DECLINE,
            data={"reason": "not today"},
        ),
    )
    ch = A2HApprovalChannel(provider, store)
    await ch.resolve_via_a2h("a2h-2", context="register", principal_id="human-1")
    assert await check_approval_status(store, "a2h-2") == "denied"
    st = await store.get("a2h-2")
    assert st is not None
    assert st.deny_reason == "not today"


@pytest.mark.asyncio
async def test_pending_escalation_merges_later_capability_specs() -> None:
    store = InMemoryApprovalStore()
    first = await create_device_authorization(
        store,
        "esc-merge",
        ["file:read"],
        capability_specs=[{"name": "file:read", "constraints": {"path": "/tmp"}}],
        approval_kind="escalation",
    )
    second = await create_device_authorization(
        store,
        "esc-merge",
        ["file:write"],
        capability_specs=[{"name": "file:write", "constraints": {"path": "/var"}}],
        approval_kind="escalation",
    )
    assert second.user_code == first.user_code
    state = await store.get("esc-merge")
    assert state is not None
    assert [spec["name"] for spec in state.capability_specs] == ["file:read", "file:write"]
    assert state.capabilities == ["file:read", "file:write"]


@pytest.mark.asyncio
async def test_pending_escalation_later_spec_replaces_same_name() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(
        store,
        "esc-replace",
        ["cap:x"],
        capability_specs=[{"name": "cap:x", "constraints": {"max": 1}}],
        approval_kind="escalation",
    )
    await create_device_authorization(
        store,
        "esc-replace",
        ["cap:x"],
        capability_specs=[{"name": "cap:x", "constraints": {"max": 99}}],
        approval_kind="escalation",
    )
    state = await store.get("esc-replace")
    assert state is not None
    assert state.capability_specs == [{"name": "cap:x", "constraints": {"max": 99}}]


@pytest.mark.asyncio
async def test_pending_ciba_merges_later_capability_specs() -> None:
    store = InMemoryApprovalStore()
    await create_ciba_approval(
        store,
        "ciba-merge",
        ["read"],
        capability_specs=[{"name": "read"}],
        approval_kind="escalation",
    )
    await create_ciba_approval(
        store,
        "ciba-merge",
        ["write"],
        capability_specs=[{"name": "write"}],
        approval_kind="escalation",
    )
    state = await store.get("ciba-merge")
    assert state is not None
    assert [spec["name"] for spec in state.capability_specs] == ["read", "write"]


@pytest.mark.asyncio
async def test_capability_specs_round_trip_on_store() -> None:
    store = InMemoryApprovalStore()
    specs: list[dict[str, Any]] = [{"name": "cap:x", "constraints": {"max": 1}}]
    await create_device_authorization(store, "cs-1", ["cap:x"], capability_specs=specs)
    st = await store.get("cs-1")
    assert st is not None
    assert st.capability_specs == specs


@pytest.mark.asyncio
async def test_approve_raises_key_error_when_missing() -> None:
    store = InMemoryApprovalStore()
    with pytest.raises(KeyError, match="No approval request for agent_id=missing"):
        await store.approve("missing", "user-1")


@pytest.mark.asyncio
async def test_deny_raises_key_error_when_missing() -> None:
    store = InMemoryApprovalStore()
    with pytest.raises(KeyError, match="No approval request for agent_id=missing"):
        await store.deny("missing", "not trusted")


@pytest.mark.asyncio
async def test_approve_raises_when_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryApprovalStore()
    frozen = datetime(2022, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("asap.auth.approval._utcnow", lambda: frozen)
    await create_device_authorization(store, "exp-approve", ["read"], expires_in=30)
    monkeypatch.setattr(
        "asap.auth.approval._utcnow",
        lambda: frozen + timedelta(seconds=60),
    )
    with pytest.raises(ValueError, match="Approval request expired"):
        await store.approve("exp-approve", "user-1")


@pytest.mark.asyncio
async def test_deny_raises_when_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryApprovalStore()
    frozen = datetime(2022, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("asap.auth.approval._utcnow", lambda: frozen)
    await create_device_authorization(store, "exp-deny", ["read"], expires_in=30)
    monkeypatch.setattr(
        "asap.auth.approval._utcnow",
        lambda: frozen + timedelta(seconds=60),
    )
    with pytest.raises(ValueError, match="Approval request expired"):
        await store.deny("exp-deny", "too late")


@pytest.mark.asyncio
async def test_approve_raises_when_not_pending() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(store, "done-1", ["read"])
    await store.approve("done-1", "user-1")
    with pytest.raises(ValueError, match="Cannot approve request in status 'approved'"):
        await store.approve("done-1", "user-2")


@pytest.mark.asyncio
async def test_deny_raises_when_not_pending() -> None:
    store = InMemoryApprovalStore()
    await create_device_authorization(store, "done-2", ["read"])
    await store.deny("done-2", "no")
    with pytest.raises(ValueError, match="Cannot deny request in status 'denied'"):
        await store.deny("done-2", "again")


class _GetAlwaysNoneApprovalStore:
    """Broken store: create succeeds but get always returns None (internal error path)."""

    async def create(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)

    async def get(self, agent_id: str) -> None:
        _ = agent_id


@pytest.mark.asyncio
async def test_create_device_authorization_raises_when_get_missing_after_create() -> None:
    store = _GetAlwaysNoneApprovalStore()
    with pytest.raises(RuntimeError, match="missing after create"):
        await create_device_authorization(store, "broken-1", ["read"])
