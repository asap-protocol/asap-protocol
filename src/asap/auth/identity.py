"""Host and agent session models for per-runtime identity (Ed25519)."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from joserfc.errors import JoseError
from joserfc.jwk import OKPKey
from pydantic import AfterValidator, Field

from asap.models.base import ASAPBaseModel

HostStatus = Literal["active", "pending", "revoked"]
AgentMode = Literal["delegated", "autonomous"]
AgentSessionStatus = Literal["pending", "active", "expired", "revoked", "rejected"]


def validate_okp_public_key(value: dict[str, Any]) -> dict[str, Any]:
    """Validate that the dict represents a valid OKP (Ed25519) public JWK."""
    try:
        OKPKey.import_key({str(k): v for k, v in value.items()})
    except (JoseError, TypeError, ValueError, KeyError) as exc:
        msg = f"Invalid OKP public key JWK: {exc}"
        raise ValueError(msg) from exc
    return value


OkpPublicKey = Annotated[dict[str, Any], AfterValidator(validate_okp_public_key)]


class HostIdentity(ASAPBaseModel):
    """Registered host identity for the agent JWT hierarchy."""

    host_id: str
    name: str | None = None
    public_key: OkpPublicKey
    user_id: str | None = None
    default_capabilities: list[str] = Field(default_factory=list)
    status: HostStatus
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class HostStore(Protocol):
    """Persistence layer for registered host identities."""

    async def save(self, host: HostIdentity) -> None:
        """Persist or replace a host record."""
        ...

    async def get(self, host_id: str) -> HostIdentity | None:
        """Return the host by id, or None if missing."""
        ...

    async def get_by_public_key(self, thumbprint: str) -> HostIdentity | None:
        """Resolve a host by JWK thumbprint (RFC 7638 SHA-256)."""
        ...

    async def revoke(self, host_id: str) -> None:
        """Mark the host as revoked."""
        ...


class AgentSession(ASAPBaseModel):
    """Agent session bound to a host and a JWK public key."""

    agent_id: str
    host_id: str
    public_key: OkpPublicKey
    mode: AgentMode
    status: AgentSessionStatus
    session_ttl: timedelta | None = None
    max_lifetime: timedelta | None = None
    absolute_lifetime: timedelta | None = None
    activated_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime


class RevokedAgentOverwriteError(ValueError):
    """Raised when ``AgentStore.save`` would resurrect a revoked agent.

    Example:
        >>> raise RevokedAgentOverwriteError("agent-1", "active")
        Traceback (most recent call last):
            ...
        RevokedAgentOverwriteError: refusing to overwrite revoked agent 'agent-1' with status 'active'
    """

    def __init__(self, agent_id: str, attempted_status: str) -> None:
        self.agent_id = agent_id
        self.attempted_status = attempted_status
        super().__init__(
            f"refusing to overwrite revoked agent {agent_id!r} with status {attempted_status!r}"
        )


def raise_if_revoked_agent_overwrite(
    existing: AgentSession | None,
    incoming: AgentSession,
) -> None:
    """Raise if *incoming* would replace a permanently revoked row.

    Custom ``AgentStore.save`` implementations should call this after an
    atomic read of the current row and before the write, with no ``await``
    between those steps (same class of invariant as ``NonceStore.check_and_mark``).

    Example:
        >>> existing = AgentSession(
        ...     agent_id="a", host_id="h", public_key={"kty": "oct", "k": "x"},
        ...     mode="delegated", status="revoked",
        ...     created_at=datetime.now(timezone.utc),
        ... )
        >>> incoming = existing.model_copy(update={"status": "active"})
        >>> raise_if_revoked_agent_overwrite(existing, incoming)
        Traceback (most recent call last):
            ...
        RevokedAgentOverwriteError: refusing to overwrite revoked agent 'a' with status 'active'
    """
    if existing is None or existing.status != "revoked" or incoming.status == "revoked":
        return
    raise RevokedAgentOverwriteError(incoming.agent_id, incoming.status)


@runtime_checkable
class AgentStore(Protocol):
    """Persistence layer for agent sessions under a host."""

    async def save(self, agent: AgentSession) -> None:
        """Persist or replace an agent session.

        Implementations MUST atomically refuse to replace a ``revoked`` row with
        a non-revoked snapshot and raise :class:`RevokedAgentOverwriteError`.
        The check and write must not yield between observing revoked status and
        committing, matching :meth:`NonceStore.check_and_mark`.
        """
        ...

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict[str, Any],
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        """Atomically slide ``last_used_at`` if this is still the same live session.

        Must be one compare-and-set, equivalent to::

            UPDATE agents
               SET last_used_at = :ts
             WHERE agent_id = :id
               AND status = 'active'
               AND host_id = :host
               AND public_key thumbprint = RFC 7638(:expected)

        Return the updated row, or ``None`` when the predicate fails (missing,
        unusable, expired, re-hosted, or rotated). Do **not** implement this as
        get → mutate → :meth:`save` of a verify-time snapshot: that TOCTOU can
        resurrect a revoked agent or undo key rotation (LIFE-005). Same class as
        ``NonceStore.check_and_mark``.

        Example:
            >>> updated = await store.touch_if_current(
            ...     agent_id, jwk, now, expected_host_id=host_id
            ... )
            >>> if updated is None:
            ...     raise RuntimeError("session changed during verify")
        """
        ...

    async def get(self, agent_id: str) -> AgentSession | None:
        """Return the session by agent id, or None if missing."""
        ...

    async def list_by_host(self, host_id: str) -> list[AgentSession]:
        """List all agent sessions for a host."""
        ...

    async def revoke(self, agent_id: str) -> None:
        """Revoke a single agent session."""
        ...

    async def revoke_by_host(self, host_id: str) -> None:
        """Revoke every agent session belonging to the host."""
        ...


def jwk_thumbprint_sha256(public_key: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint using SHA-256 (base64url, no padding).

    Uses only the required members for the key type (RFC 7638 §3.2), in
    lexicographic order via ``sort_keys=True``, so optional JWK fields
    (``kid``, ``use``, etc.) do not change the thumbprint.
    """
    kty = public_key.get("kty")
    if kty == "OKP":
        required = {"crv": public_key["crv"], "kty": kty, "x": public_key["x"]}
    elif kty == "EC":
        required = {
            "crv": public_key["crv"],
            "kty": kty,
            "x": public_key["x"],
            "y": public_key["y"],
        }
    elif kty == "RSA":
        required = {"e": public_key["e"], "kty": kty, "n": public_key["n"]}
    elif kty == "oct":
        required = {"k": public_key["k"], "kty": kty}
    else:
        msg = f"unsupported kty for thumbprint: {kty!r}"
        raise ValueError(msg)
    canonical = json.dumps(required, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def host_urn_from_thumbprint(thumbprint: str) -> str:
    """Synthetic host id for a first-seen host key (``iss`` thumbprint)."""
    return f"urn:asap:host:{thumbprint}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryHostStore:
    """In-memory `HostStore` for development and tests.

    Optionally cascades host revocation to an `AgentStore` when provided.
    """

    def __init__(self, agent_store: AgentStore | None = None) -> None:
        self._hosts: dict[str, HostIdentity] = {}
        self._thumb_to_host_id: dict[str, str] = {}
        self._agent_store = agent_store

    async def save(self, host: HostIdentity) -> None:
        """Persist or replace a host and refresh the thumbprint index."""
        previous = self._hosts.get(host.host_id)
        if previous is not None:
            old_tp = jwk_thumbprint_sha256(previous.public_key)
            new_tp = jwk_thumbprint_sha256(host.public_key)
            if old_tp != new_tp and self._thumb_to_host_id.get(old_tp) == host.host_id:
                del self._thumb_to_host_id[old_tp]
        thumb = jwk_thumbprint_sha256(host.public_key)
        self._thumb_to_host_id[thumb] = host.host_id
        self._hosts[host.host_id] = host

    async def get(self, host_id: str) -> HostIdentity | None:
        return self._hosts.get(host_id)

    async def get_by_public_key(self, thumbprint: str) -> HostIdentity | None:
        host_id = self._thumb_to_host_id.get(thumbprint)
        if host_id is None:
            return None
        return self._hosts.get(host_id)

    async def revoke(self, host_id: str) -> None:
        host = self._hosts.get(host_id)
        if host is None or host.status == "revoked":
            return
        now = _utc_now()
        updated = host.model_copy(update={"status": "revoked", "updated_at": now})
        self._hosts[host_id] = updated
        if self._agent_store is not None:
            await self._agent_store.revoke_by_host(host_id)


class InMemoryAgentStore:
    """In-memory `AgentStore` for development and tests.

    ``save`` refuses to replace a ``revoked`` row with a non-revoked snapshot
    (:class:`RevokedAgentOverwriteError`) so stale get→mutate→full-row-save
    races cannot resurrect revoked agents. The check and dict write have no
    ``await`` between them.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentSession] = {}

    async def save(self, agent: AgentSession) -> None:
        existing = self._agents.get(agent.agent_id)
        raise_if_revoked_agent_overwrite(existing, agent)
        self._agents[agent.agent_id] = agent

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict[str, Any],
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        # Read-and-assign with no await so a single event loop cannot interleave
        # revoke/rotate between the predicate and the last_used_at write.
        current = self._agents.get(agent_id)
        if current is None or current.status != "active":
            return None
        if current.host_id != expected_host_id:
            return None
        try:
            expected_tp = jwk_thumbprint_sha256(expected_public_key)
            current_tp = jwk_thumbprint_sha256(current.public_key)
        except (KeyError, TypeError, ValueError):
            return None
        if current_tp != expected_tp:
            return None
        if check_agent_expiry(current) != "active":
            return None
        updated = current.model_copy(update={"last_used_at": last_used_at})
        self._agents[agent_id] = updated
        return updated

    async def get(self, agent_id: str) -> AgentSession | None:
        return self._agents.get(agent_id)

    async def list_by_host(self, host_id: str) -> list[AgentSession]:
        return sorted(
            (a for a in self._agents.values() if a.host_id == host_id),
            key=lambda s: s.agent_id,
        )

    async def revoke(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent is None or agent.status == "revoked":
            return
        self._agents[agent_id] = agent.model_copy(update={"status": "revoked"})

    async def revoke_by_host(self, host_id: str) -> None:
        for aid in [a.agent_id for a in self._agents.values() if a.host_id == host_id]:
            await self.revoke(aid)


async def save_agent_unless_revoked(agent_store: AgentStore, agent: AgentSession) -> None:
    """Persist *agent* via :meth:`AgentStore.save`.

    The atomic revoked-row guard lives on ``save`` itself (implementations MUST
    raise :class:`RevokedAgentOverwriteError`). This helper is a named call-site
    for get→mutate→full-row writes; it does not re-get.

    Example:
        >>> import asyncio
        >>> store = InMemoryAgentStore()
        >>> asyncio.run(save_agent_unless_revoked(store, AgentSession(
        ...     agent_id="a", host_id="h", public_key={"kty": "oct", "k": "x"},
        ...     mode="delegated", status="active",
        ...     created_at=datetime.now(timezone.utc),
        ... )))
    """
    await agent_store.save(agent)


# ---------------------------------------------------------------------------
# Agent session lifecycle (session TTL, max lifetime, absolute lifetime)
# ---------------------------------------------------------------------------

ExpiryStatus = Literal["active", "expired", "revoked"]


def check_agent_expiry(agent: AgentSession) -> ExpiryStatus:
    """Return ``active``, ``expired``, or ``revoked`` (absolute limit exceeded).

    Example:
        >>> from datetime import datetime, timezone
        >>> agent = AgentSession(
        ...     agent_id="a", host_id="h", public_key={"kty": "oct", "k": "x"},
        ...     mode="delegated", status="active", created_at=datetime.now(timezone.utc),
        ... )
        >>> check_agent_expiry(agent)
        'active'
    """
    now = _utc_now()

    if agent.absolute_lifetime is not None and now - agent.created_at > agent.absolute_lifetime:
        return "revoked"

    if (
        agent.max_lifetime is not None
        and agent.activated_at is not None
        and now - agent.activated_at > agent.max_lifetime
    ):
        return "expired"

    if (
        agent.session_ttl is not None
        and agent.last_used_at is not None
        and now - agent.last_used_at > agent.session_ttl
    ):
        return "expired"

    return "active"


def extend_session(agent: AgentSession) -> AgentSession:
    """Update ``last_used_at`` to now, keeping the session alive."""
    return agent.model_copy(update={"last_used_at": _utc_now()})


def reactivate_agent(
    agent: AgentSession,
    _host: HostIdentity,
) -> AgentSession:
    """Reset activation and last-used time; fail if revoked or past absolute lifetime.

    Raises:
        ValueError: If the agent is permanently revoked or has exceeded its
            absolute lifetime (reactivation is not possible in either case).
    """
    now = _utc_now()

    if agent.status == "revoked":
        msg = f"Agent {agent.agent_id} is permanently revoked"
        raise ValueError(msg)

    if agent.absolute_lifetime is not None and now - agent.created_at > agent.absolute_lifetime:
        msg = f"Agent {agent.agent_id} has exceeded absolute lifetime; reactivation is not possible"
        raise ValueError(msg)

    return agent.model_copy(
        update={
            "status": "active",
            "activated_at": now,
            "last_used_at": now,
        }
    )
