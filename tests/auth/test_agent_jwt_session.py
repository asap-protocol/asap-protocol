"""Tests for Agent JWT session slide result typing (LIFE-005)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest

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


async def test_slide_session_refuses_idle_ttl_without_extending() -> None:
    """Re-read must return agent_expired and leave last_used_at unchanged."""
    store = InMemoryAgentStore()
    now = _utc_now()
    stale = now - timedelta(minutes=10)
    stored = _active_session().model_copy(
        update={"session_ttl": timedelta(minutes=5), "last_used_at": stale}
    )
    await store.save(stored)
    slid = await slide_session_if_still_current(store, stored)
    assert slid.ok is False
    assert slid.session is None
    assert slid.error == "agent_expired"
    kept = await store.get("a1")
    assert kept is not None and kept.last_used_at == stale


async def test_slide_session_refuses_absolute_lifetime_without_extending() -> None:
    """Absolute lifetime on re-read is agent_revoked, not a session slide."""
    store = InMemoryAgentStore()
    now = _utc_now()
    stale = now - timedelta(minutes=1)
    stored = _active_session().model_copy(
        update={
            "created_at": now - timedelta(days=2),
            "absolute_lifetime": timedelta(days=1),
            "last_used_at": stale,
        }
    )
    await store.save(stored)
    slid = await slide_session_if_still_current(store, stored)
    assert slid.ok is False
    assert slid.error == "agent_revoked"
    kept = await store.get("a1")
    assert kept is not None
    assert kept.status == "active"
    assert kept.last_used_at == stale


@pytest.mark.parametrize("status", ["pending", "rejected"])
async def test_slide_session_refuses_unapproved_status_on_reread(
    status: Literal["pending", "rejected"],
) -> None:
    """A registration status written before touch must not extend the session."""
    store = InMemoryAgentStore()
    now = _utc_now()
    stale = now - timedelta(minutes=5)
    verified = _active_session()
    await store.save(verified.model_copy(update={"status": status, "last_used_at": stale}))
    slid = await slide_session_if_still_current(store, verified)
    assert slid.ok is False
    assert slid.error == f"agent session not usable: {status}"
    kept = await store.get("a1")
    assert kept is not None
    assert kept.status == status
    assert kept.last_used_at == stale
