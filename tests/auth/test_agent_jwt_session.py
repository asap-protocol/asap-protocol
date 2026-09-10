"""Tests for Agent JWT session slide result typing (LIFE-005)."""

from __future__ import annotations

from datetime import datetime, timezone

from asap.auth.agent_jwt_session import SessionSlideResult, slide_session_if_still_current
from asap.auth.identity import AgentSession, InMemoryAgentStore
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _active_session(*, agent_id: str = "a1", host_id: str = "h1") -> AgentSession:
    return AgentSession(
        agent_id=agent_id,
        host_id=host_id,
        public_key=make_ed25519_jwk(),
        mode="delegated",
        status="active",
        created_at=_utc_now(),
    )


def test_session_slide_result_ok_requires_session_and_no_error() -> None:
    """``ok`` is true only when a session is present without an error string."""
    assert SessionSlideResult(error="unknown agent").ok is False
    assert SessionSlideResult().ok is False


async def test_slide_session_returns_error_for_unknown_agent() -> None:
    """Missing row is an error result, not a raised exception."""
    store = InMemoryAgentStore()
    slid = await slide_session_if_still_current(store, _active_session())
    assert slid.ok is False
    assert slid.session is None
    assert slid.error == "unknown agent"


async def test_slide_session_returns_session_when_touch_succeeds() -> None:
    """Successful CAS returns the persisted row on ``session``."""
    store = InMemoryAgentStore()
    agent = _active_session()
    await store.save(agent)
    slid = await slide_session_if_still_current(store, agent)
    assert slid.ok is True
    assert slid.error is None
    assert slid.session is not None
    assert slid.session.agent_id == "a1"
    assert slid.session.last_used_at is not None
