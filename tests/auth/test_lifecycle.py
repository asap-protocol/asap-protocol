"""Tests for agent lifecycle — lifetime clocks, expiry, and reactivation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest

from asap.auth.identity import (
    AgentSession,
    ExpiryStatus,
    HostIdentity,
    check_agent_expiry,
    extend_session,
    reactivate_agent,
)
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_VALID_JWK = make_ed25519_jwk()


def _agent(
    *,
    status: str = "active",
    session_ttl: timedelta | None = None,
    max_lifetime: timedelta | None = None,
    absolute_lifetime: timedelta | None = None,
    created_at: datetime | None = None,
    activated_at: datetime | None = None,
    last_used_at: datetime | None = None,
) -> AgentSession:
    now = _utc_now()
    return AgentSession(
        agent_id="agent-test",
        host_id="host-test",
        public_key=_VALID_JWK,
        mode="delegated",
        status=status,
        session_ttl=session_ttl,
        max_lifetime=max_lifetime,
        absolute_lifetime=absolute_lifetime,
        created_at=created_at or now,
        activated_at=activated_at,
        last_used_at=last_used_at,
    )


def _host() -> HostIdentity:
    now = _utc_now()
    return HostIdentity(
        host_id="host-test",
        public_key=_VALID_JWK,
        status="active",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# check_agent_expiry
# ---------------------------------------------------------------------------


class TestCheckAgentExpiry:
    def test_active_no_clocks(self) -> None:
        assert check_agent_expiry(_agent()) == "active"

    def test_all_statuses_reachable(self) -> None:
        """All ExpiryStatus values can be produced."""
        statuses = get_args(ExpiryStatus)
        assert set(statuses) == {"active", "expired", "revoked"}

    # -- session TTL --------------------------------------------------------

    def test_session_ttl_active(self) -> None:
        now = _utc_now()
        a = _agent(
            session_ttl=timedelta(minutes=30),
            last_used_at=now - timedelta(minutes=5),
        )
        assert check_agent_expiry(a) == "active"

    def test_session_ttl_expired(self) -> None:
        now = _utc_now()
        a = _agent(
            session_ttl=timedelta(minutes=5),
            last_used_at=now - timedelta(minutes=10),
        )
        assert check_agent_expiry(a) == "expired"

    def test_session_ttl_just_within(self) -> None:
        """Well within TTL — still active."""
        now = _utc_now()
        a = _agent(
            session_ttl=timedelta(minutes=10),
            last_used_at=now - timedelta(minutes=9),
        )
        assert check_agent_expiry(a) == "active"

    def test_session_ttl_without_last_used(self) -> None:
        """session_ttl set but last_used_at is None — skip TTL check."""
        a = _agent(session_ttl=timedelta(minutes=5))
        assert check_agent_expiry(a) == "active"

    # -- max lifetime -------------------------------------------------------

    def test_max_lifetime_active(self) -> None:
        now = _utc_now()
        a = _agent(
            max_lifetime=timedelta(hours=2),
            activated_at=now - timedelta(hours=1),
        )
        assert check_agent_expiry(a) == "active"

    def test_max_lifetime_expired(self) -> None:
        now = _utc_now()
        a = _agent(
            max_lifetime=timedelta(hours=1),
            activated_at=now - timedelta(hours=2),
        )
        assert check_agent_expiry(a) == "expired"

    def test_max_lifetime_without_activated_at(self) -> None:
        """max_lifetime set but activated_at is None — skip check."""
        a = _agent(max_lifetime=timedelta(hours=1))
        assert check_agent_expiry(a) == "active"

    # -- absolute lifetime --------------------------------------------------

    def test_absolute_lifetime_active(self) -> None:
        now = _utc_now()
        a = _agent(
            absolute_lifetime=timedelta(days=7),
            created_at=now - timedelta(days=3),
        )
        assert check_agent_expiry(a) == "active"

    def test_absolute_lifetime_revoked(self) -> None:
        now = _utc_now()
        a = _agent(
            absolute_lifetime=timedelta(days=1),
            created_at=now - timedelta(days=2),
        )
        assert check_agent_expiry(a) == "revoked"

    # -- combined / precedence ----------------------------------------------

    def test_absolute_takes_precedence_over_expired(self) -> None:
        """If both max_lifetime and absolute_lifetime exceeded, revoked wins."""
        now = _utc_now()
        a = _agent(
            max_lifetime=timedelta(hours=1),
            activated_at=now - timedelta(hours=2),
            absolute_lifetime=timedelta(hours=1),
            created_at=now - timedelta(hours=2),
        )
        assert check_agent_expiry(a) == "revoked"

    def test_expired_when_only_session_ttl_exceeded(self) -> None:
        now = _utc_now()
        a = _agent(
            session_ttl=timedelta(minutes=5),
            last_used_at=now - timedelta(minutes=10),
            absolute_lifetime=timedelta(days=30),
            created_at=now,
        )
        assert check_agent_expiry(a) == "expired"


# ---------------------------------------------------------------------------
# extend_session
# ---------------------------------------------------------------------------


class TestExtendSession:
    def test_updates_last_used_at(self) -> None:
        old = _utc_now() - timedelta(minutes=10)
        a = _agent(last_used_at=old)
        extended = extend_session(a)
        assert extended.last_used_at is not None
        assert extended.last_used_at > old

    def test_preserves_other_fields(self) -> None:
        a = _agent(session_ttl=timedelta(minutes=30))
        extended = extend_session(a)
        assert extended.agent_id == a.agent_id
        assert extended.session_ttl == a.session_ttl
        assert extended.created_at == a.created_at


# ---------------------------------------------------------------------------
# reactivate_agent
# ---------------------------------------------------------------------------


class TestReactivateAgent:
    def test_success(self) -> None:
        a = _agent(
            status="expired",
            activated_at=_utc_now() - timedelta(hours=2),
        )
        reactivated = reactivate_agent(a, _host())
        assert reactivated.status == "active"
        assert reactivated.activated_at is not None
        assert reactivated.last_used_at is not None

    def test_reactivation_resets_clocks(self) -> None:
        old_activated = _utc_now() - timedelta(hours=5)
        a = _agent(status="expired", activated_at=old_activated)
        reactivated = reactivate_agent(a, _host())
        assert reactivated.activated_at is not None
        assert reactivated.activated_at > old_activated

    def test_absolute_lifetime_exceeded_raises(self) -> None:
        a = _agent(
            status="expired",
            absolute_lifetime=timedelta(days=1),
            created_at=_utc_now() - timedelta(days=2),
        )
        with pytest.raises(ValueError, match="absolute lifetime"):
            reactivate_agent(a, _host())

    def test_revoked_agent_raises(self) -> None:
        a = _agent(status="revoked")
        with pytest.raises(ValueError, match="permanently revoked"):
            reactivate_agent(a, _host())

    def test_rejected_agent_raises(self) -> None:
        """Rejected registration must not be force-activated via reactivate (LIFE-004)."""
        a = _agent(status="rejected")
        with pytest.raises(ValueError, match="rejected"):
            reactivate_agent(a, _host())

    def test_pending_agent_raises(self) -> None:
        """Pending approval must not be skipped by reactivate (approval bypass)."""
        a = _agent(status="pending")
        with pytest.raises(ValueError, match="pending approval"):
            reactivate_agent(a, _host())

    def test_active_agent_reactivation_succeeds(self) -> None:
        """Reactivating an already-active agent is a no-op refresh."""
        a = _agent(status="active")
        result = reactivate_agent(a, _host())
        assert result.status == "active"

    def test_preserves_created_at(self) -> None:
        created = _utc_now() - timedelta(days=3)
        a = _agent(status="expired", created_at=created)
        reactivated = reactivate_agent(a, _host())
        assert reactivated.created_at == created
