"""Approval objects and flows for agent registration (Device Auth, CIBA)."""

from __future__ import annotations

__all__ = [
    "A2HApprovalChannel",
    "ApprovalMethod",
    "ApprovalObject",
    "ApprovalRequestState",
    "ApprovalStatus",
    "ApprovalStore",
    "DEFAULT_CIBA_BINDING_MESSAGE",
    "DEFAULT_DEVICE_EXPIRES_IN_SECONDS",
    "DEFAULT_DEVICE_VERIFICATION_URI",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "InMemoryApprovalStore",
    "USER_CODE_ALPHABET",
    "USER_CODE_LENGTH",
    "approval_object_for_client",
    "check_approval_status",
    "create_ciba_approval",
    "create_device_authorization",
    "select_approval_method",
    "ApprovalKind",
]

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from asap.auth.identity import AgentSession, HostIdentity
from asap.handlers.hitl import ApprovalDecision, HumanApprovalProvider
from asap.models.base import ASAPBaseModel

ApprovalMethod = Literal["device_authorization", "ciba"]
ApprovalStatus = Literal["pending", "approved", "denied", "expired"]
ApprovalKind = Literal["registration", "escalation"]

USER_CODE_LENGTH = 8
USER_CODE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

DEFAULT_DEVICE_VERIFICATION_URI = "https://asap.local/device"
DEFAULT_DEVICE_EXPIRES_IN_SECONDS = 600
DEFAULT_POLL_INTERVAL_SECONDS = 5

DEFAULT_CIBA_BINDING_MESSAGE = "Approve ASAP agent registration"


class ApprovalObject(ASAPBaseModel):
    """Approval payload returned when registration or consent requires user action.

    Shape depends on ``method``: Device Authorization (RFC 8628) exposes
    ``verification_uri*`` and ``user_code``; CIBA may expose ``binding_message``
    instead, without browser verification URIs.
    """

    method: ApprovalMethod
    verification_uri: str | None = None
    verification_uri_complete: str | None = None
    user_code: str | None = None
    binding_message: str | None = None
    expires_in: int = Field(ge=1, description="Seconds until the approval request expires.")
    interval: int = Field(
        ge=1,
        description="Minimum seconds the client should wait between poll attempts.",
    )


class ApprovalRequestState(ASAPBaseModel):
    """Server-side approval request state (including after resolution)."""

    agent_id: str
    method: ApprovalMethod
    capabilities: list[str]
    capability_specs: list[dict[str, Any]] = Field(default_factory=list)
    approval_kind: ApprovalKind = "registration"
    status: ApprovalStatus
    user_code: str | None = None
    verification_uri: str | None = None
    verification_uri_complete: str | None = None
    binding_message: str | None = None
    expires_in: int = Field(ge=1)
    interval: int = Field(ge=1)
    created_at: datetime
    approved_by: str | None = None
    deny_reason: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_user_code() -> str:
    """RFC 8628-style user code: fixed-length uppercase alphanumeric (no separator)."""
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def _deadline(state: ApprovalRequestState) -> datetime:
    return state.created_at + timedelta(seconds=state.expires_in)


def _refresh_expired(state: ApprovalRequestState) -> ApprovalRequestState:
    if state.status != "pending":
        return state
    if _utcnow() >= _deadline(state):
        return state.model_copy(update={"status": "expired"})
    return state


def _state_to_approval_object(state: ApprovalRequestState) -> ApprovalObject:
    """Build the client-facing approval object from stored state.

    ``expires_in`` is the remaining lifetime in seconds (RFC 8628-style), not the
    original TTL stored in ``ApprovalRequestState.expires_in``.
    """
    remaining = max(1, int((_deadline(state) - _utcnow()).total_seconds()))
    return ApprovalObject(
        method=state.method,
        verification_uri=state.verification_uri,
        verification_uri_complete=state.verification_uri_complete,
        user_code=state.user_code,
        binding_message=state.binding_message,
        expires_in=remaining,
        interval=state.interval,
    )


def approval_object_for_client(state: ApprovalRequestState) -> ApprovalObject:
    """Return the API ``ApprovalObject`` for a stored approval request."""
    return _state_to_approval_object(state)


def select_approval_method(
    host: HostIdentity,
    agent: AgentSession | None = None,
    *,
    host_supports_ciba: bool = True,
    preferred_method: ApprovalMethod | None = None,
    agent_controls_browser: bool = False,
) -> ApprovalMethod:
    """Choose Device Authorization vs CIBA based on host binding and server policy.

    If the host is linked (``user_id`` set) and CIBA is supported, CIBA is preferred
    unless the client hints ``device_authorization``. The server may ignore an
    unsupported ``preferred_method`` (e.g. CIBA when the host is unlinked).

    When ``agent_controls_browser`` is true and CIBA is available, CIBA is forced
    so approval can complete on a separate device (mitigates self-approval).
    """
    _ = agent  # Reserved for future policy (e.g. autonomous vs delegated).
    linked = bool(host.user_id and str(host.user_id).strip())
    can_ciba = linked and host_supports_ciba

    if agent_controls_browser and can_ciba:
        return "ciba"

    if preferred_method == "device_authorization":
        return "device_authorization"
    if preferred_method == "ciba":
        return "ciba" if can_ciba else "device_authorization"
    if can_ciba:
        return "ciba"
    return "device_authorization"


@runtime_checkable
class ApprovalStore(Protocol):
    """Persistence for in-flight approval requests (Device Auth, CIBA)."""

    async def create(
        self,
        agent_id: str,
        method: ApprovalMethod,
        *,
        capabilities: list[str],
        expires_in: int,
        interval: int,
        user_code: str | None = None,
        verification_uri: str | None = None,
        verification_uri_complete: str | None = None,
        binding_message: str | None = None,
        capability_specs: list[dict[str, Any]] | None = None,
        approval_kind: ApprovalKind = "registration",
    ) -> None:
        """Create a new pending approval request for ``agent_id``."""
        ...

    async def remove(self, agent_id: str) -> None:
        """Remove any approval record for ``agent_id`` (e.g. after escalation is applied)."""
        ...

    async def get(self, agent_id: str) -> ApprovalRequestState | None:
        """Return current approval state, or ``None`` if no request exists."""
        ...

    async def approve(self, agent_id: str, user_id: str) -> None:
        """Mark the request approved by ``user_id``."""
        ...

    async def deny(self, agent_id: str, reason: str) -> None:
        """Mark the request denied with a short ``reason``."""
        ...

    async def update_pending_capabilities(
        self,
        agent_id: str,
        *,
        capabilities: list[str],
        capability_specs: list[dict[str, Any]],
    ) -> None:
        """Replace capabilities/specs on a pending request; keep codes and ``created_at``."""
        ...


class InMemoryApprovalStore:
    """Process-local approval store for tests and simple deployments."""

    def __init__(self) -> None:
        self._by_agent: dict[str, ApprovalRequestState] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        agent_id: str,
        method: ApprovalMethod,
        *,
        capabilities: list[str],
        expires_in: int,
        interval: int,
        user_code: str | None = None,
        verification_uri: str | None = None,
        verification_uri_complete: str | None = None,
        binding_message: str | None = None,
        capability_specs: list[dict[str, Any]] | None = None,
        approval_kind: ApprovalKind = "registration",
    ) -> None:
        specs = [dict(x) for x in capability_specs] if capability_specs else []
        state = ApprovalRequestState(
            agent_id=agent_id,
            method=method,
            capabilities=list(capabilities),
            capability_specs=specs,
            approval_kind=approval_kind,
            status="pending",
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            binding_message=binding_message,
            expires_in=expires_in,
            interval=interval,
            created_at=_utcnow(),
        )
        async with self._lock:
            self._by_agent[agent_id] = state

    async def remove(self, agent_id: str) -> None:
        async with self._lock:
            self._by_agent.pop(agent_id, None)

    async def get(self, agent_id: str) -> ApprovalRequestState | None:
        async with self._lock:
            raw = self._by_agent.get(agent_id)
            if raw is None:
                return None
            updated = _refresh_expired(raw)
            if updated is not raw:
                self._by_agent[agent_id] = updated
            return updated

    async def approve(self, agent_id: str, user_id: str) -> None:
        async with self._lock:
            raw = self._by_agent.get(agent_id)
            if raw is None:
                msg = f"No approval request for agent_id={agent_id}"
                raise KeyError(msg)
            current = _refresh_expired(raw)
            if current.status == "expired":
                self._by_agent[agent_id] = current
                msg = "Approval request expired"
                raise ValueError(msg)
            if current.status != "pending":
                msg = f"Cannot approve request in status {current.status!r}"
                raise ValueError(msg)
            self._by_agent[agent_id] = current.model_copy(
                update={"status": "approved", "approved_by": user_id},
            )

    async def deny(self, agent_id: str, reason: str) -> None:
        async with self._lock:
            raw = self._by_agent.get(agent_id)
            if raw is None:
                msg = f"No approval request for agent_id={agent_id}"
                raise KeyError(msg)
            current = _refresh_expired(raw)
            if current.status == "expired":
                self._by_agent[agent_id] = current
                msg = "Approval request expired"
                raise ValueError(msg)
            if current.status != "pending":
                msg = f"Cannot deny request in status {current.status!r}"
                raise ValueError(msg)
            self._by_agent[agent_id] = current.model_copy(
                update={"status": "denied", "deny_reason": reason},
            )

    async def update_pending_capabilities(
        self,
        agent_id: str,
        *,
        capabilities: list[str],
        capability_specs: list[dict[str, Any]],
    ) -> None:
        """Replace capabilities/specs on a pending request; keep codes and ``created_at``."""
        async with self._lock:
            raw = self._by_agent.get(agent_id)
            if raw is None:
                msg = f"No approval request for agent_id={agent_id}"
                raise KeyError(msg)
            current = _refresh_expired(raw)
            if current.status == "expired":
                self._by_agent[agent_id] = current
                msg = "Approval request expired"
                raise ValueError(msg)
            if current.status != "pending":
                msg = f"Cannot update request in status {current.status!r}"
                raise ValueError(msg)
            self._by_agent[agent_id] = current.model_copy(
                update={
                    "capabilities": list(capabilities),
                    "capability_specs": [dict(spec) for spec in capability_specs],
                },
            )


def _spec_name(spec: dict[str, Any]) -> str:
    return str(spec.get("name", ""))


def _merge_capability_specs(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union specs by ``name``; a later request replaces the same name."""
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for spec in [*existing, *incoming]:
        name = _spec_name(spec)
        if name not in by_name:
            order.append(name)
        by_name[name] = dict(spec)
    return [by_name[name] for name in order]


async def _reuse_or_merge_pending(
    store: ApprovalStore,
    existing: ApprovalRequestState,
    *,
    capabilities: list[str],
    capability_specs: list[dict[str, Any]] | None,
) -> ApprovalObject:
    """Keep the pending Device/CIBA challenge; merge later capability payloads."""
    incoming = [dict(spec) for spec in capability_specs] if capability_specs else []
    merged_specs = _merge_capability_specs(existing.capability_specs, incoming)
    merged_caps = list(dict.fromkeys([*existing.capabilities, *capabilities]))
    if merged_specs == existing.capability_specs and merged_caps == list(existing.capabilities):
        return _state_to_approval_object(existing)
    await store.update_pending_capabilities(
        existing.agent_id,
        capabilities=merged_caps,
        capability_specs=merged_specs,
    )
    fresh = await store.get(existing.agent_id)
    if fresh is None:
        msg = "Internal error: approval request missing after merge"
        raise RuntimeError(msg)
    return _state_to_approval_object(fresh)


async def create_device_authorization(
    store: ApprovalStore,
    agent_id: str,
    capabilities: list[str],
    *,
    expires_in: int = DEFAULT_DEVICE_EXPIRES_IN_SECONDS,
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    verification_uri: str | None = None,
    capability_specs: list[dict[str, Any]] | None = None,
    approval_kind: ApprovalKind = "registration",
) -> ApprovalObject:
    """Start or reuse a Device Authorization (RFC 8628) approval for an agent.

    If a **pending**, non-expired request already exists for ``agent_id``, returns
    the same ``user_code`` and URIs (idempotent re-registration). A later request
    with additional or replaced ``capability_specs`` is merged into that pending
    row so the human does not approve a stale payload.
    """
    base_uri = verification_uri or DEFAULT_DEVICE_VERIFICATION_URI
    existing = await store.get(agent_id)
    if (
        existing is not None
        and existing.method == "device_authorization"
        and existing.status == "pending"
        and existing.approval_kind == approval_kind
    ):
        return await _reuse_or_merge_pending(
            store,
            existing,
            capabilities=capabilities,
            capability_specs=capability_specs,
        )

    user_code = _generate_user_code()
    verification_uri_complete = f"{base_uri}?user_code={user_code}"
    await store.create(
        agent_id,
        "device_authorization",
        capabilities=capabilities,
        expires_in=expires_in,
        interval=interval,
        user_code=user_code,
        verification_uri=base_uri,
        verification_uri_complete=verification_uri_complete,
        capability_specs=capability_specs,
        approval_kind=approval_kind,
    )
    fresh = await store.get(agent_id)
    if fresh is None:
        msg = "Internal error: approval request missing after create"
        raise RuntimeError(msg)
    return _state_to_approval_object(fresh)


async def create_ciba_approval(
    store: ApprovalStore,
    agent_id: str,
    capabilities: list[str],
    *,
    binding_message: str | None = None,
    expires_in: int = DEFAULT_DEVICE_EXPIRES_IN_SECONDS,
    interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    capability_specs: list[dict[str, Any]] | None = None,
    approval_kind: ApprovalKind = "registration",
) -> ApprovalObject:
    """Create a CIBA-style approval (no ``verification_uri``; push via separate channel)."""
    msg = binding_message if binding_message is not None else DEFAULT_CIBA_BINDING_MESSAGE
    existing = await store.get(agent_id)
    if (
        existing is not None
        and existing.method == "ciba"
        and existing.status == "pending"
        and existing.approval_kind == approval_kind
    ):
        return await _reuse_or_merge_pending(
            store,
            existing,
            capabilities=capabilities,
            capability_specs=capability_specs,
        )

    await store.create(
        agent_id,
        "ciba",
        capabilities=capabilities,
        expires_in=expires_in,
        interval=interval,
        binding_message=msg,
        capability_specs=capability_specs,
        approval_kind=approval_kind,
    )
    fresh = await store.get(agent_id)
    if fresh is None:
        msg = "Internal error: approval request missing after create"
        raise RuntimeError(msg)
    return _state_to_approval_object(fresh)


async def check_approval_status(store: ApprovalStore, agent_id: str) -> ApprovalStatus:
    """Return the current approval status, marking elapsed pending requests as expired."""
    state = await store.get(agent_id)
    if state is None:
        msg = f"No approval request for agent_id={agent_id}"
        raise KeyError(msg)
    return state.status


class A2HApprovalChannel:
    """Deliver human approval via A2H and mirror the outcome into an :class:`ApprovalStore`."""

    def __init__(self, provider: HumanApprovalProvider, store: ApprovalStore) -> None:
        self._provider = provider
        self._store = store

    async def resolve_via_a2h(
        self,
        agent_id: str,
        *,
        context: str,
        principal_id: str,
        timeout_seconds: float = 300.0,
    ) -> None:
        """Block until A2H returns, then approve or deny the pending registration."""
        result = await self._provider.request_approval(
            context=context,
            principal_id=principal_id,
            timeout_seconds=timeout_seconds,
        )
        if result.decision == ApprovalDecision.APPROVE:
            await self._store.approve(agent_id, principal_id)
            return
        reason = "declined"
        if result.data and isinstance(result.data.get("reason"), str):
            reason = str(result.data["reason"])
        await self._store.deny(agent_id, reason)
