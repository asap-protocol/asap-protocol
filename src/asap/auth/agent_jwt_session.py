"""LIFE-005 Agent JWT session slide: re-read plus atomic ``touch_if_current``.

Kept out of ``agent_jwt.py`` so verify stays a crypto/claims gate and persist is
one compare-and-set (not full-row ``save`` of a snapshot).
"""

from __future__ import annotations

from datetime import datetime, timezone

from asap.auth.identity import AgentSession, AgentStore, check_agent_expiry, jwk_thumbprint_sha256

_UNUSABLE_AGENT_STATUSES = frozenset({"revoked", "expired", "pending", "rejected"})


def unusable_agent_error(agent: AgentSession) -> str | None:
    """Error string when the session status is not authenticable, else ``None``."""
    if agent.status in _UNUSABLE_AGENT_STATUSES:
        return f"agent session not usable: {agent.status}"
    return None


def expired_agent_error(agent: AgentSession) -> str | None:
    """Error string when idle/max/absolute lifetime has elapsed, else ``None``."""
    expiry_status = check_agent_expiry(agent)
    if expiry_status == "revoked":
        return "agent_revoked"
    if expiry_status == "expired":
        return "agent_expired"
    return None


def agent_public_key_changed(current: AgentSession, verified: AgentSession) -> bool:
    """True when RFC 7638 thumbprints differ (optional JWK members ignored)."""
    return jwk_thumbprint_sha256(current.public_key) != jwk_thumbprint_sha256(verified.public_key)


async def slide_session_if_still_current(
    agent_store: AgentStore,
    verified: AgentSession,
) -> AgentSession | str:
    """Re-read then atomically touch ``last_used_at``; never full-row ``save``.

    Returns the persisted session, or an error string. The re-read yields
    specific errors; ``touch_if_current`` closes the remaining check-then-act window.

    Example:
        >>> slid = await slide_session_if_still_current(store, agent)
        >>> if isinstance(slid, str):
        ...     return JwtVerifyResult(ok=False, error=slid)
    """
    current = await agent_store.get(verified.agent_id)
    if current is None:
        return "unknown agent"
    if (unusable := unusable_agent_error(current)) is not None:
        return unusable
    if current.host_id != verified.host_id:
        return "agent host_id changed during verification"
    if agent_public_key_changed(current, verified):
        return "agent key changed during verification"
    if (expired := expired_agent_error(current)) is not None:
        return expired

    touched = await agent_store.touch_if_current(
        verified.agent_id,
        dict(verified.public_key),
        datetime.now(timezone.utc),
        expected_host_id=verified.host_id,
    )
    if touched is None:
        return "agent session changed during verification"
    return touched
