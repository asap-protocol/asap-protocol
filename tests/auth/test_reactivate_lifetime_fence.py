"""Lock ``reactivate_agent`` at the absolute-lifetime instant.

The refusal uses ``now - created_at > absolute_lifetime``. Equality must still
refresh an expired row. One microsecond later must refuse without mutating it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from asap.auth.identity import AgentSession, HostIdentity, reactivate_agent
from tests.crypto.jwk_helpers import make_ed25519_jwk

_CREATED = datetime(2024, 1, 1, tzinfo=timezone.utc)
_LIFETIME = timedelta(days=7)


def _freeze(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    def _utc_now() -> datetime:
        return instant

    monkeypatch.setattr("asap.auth.identity._utc_now", _utc_now)


def _host(now: datetime) -> HostIdentity:
    return HostIdentity(
        host_id="host-fence",
        public_key=make_ed25519_jwk(),
        status="active",
        created_at=now,
        updated_at=now,
    )


def _expired_agent() -> AgentSession:
    return AgentSession(
        agent_id="agent-fence",
        host_id="host-fence",
        public_key=make_ed25519_jwk(),
        mode="delegated",
        status="expired",
        created_at=_CREATED,
        activated_at=_CREATED,
        absolute_lifetime=_LIFETIME,
    )


def test_reactivate_agent_refreshes_at_absolute_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equality is not past the absolute lifetime, so reactivation refreshes clocks."""
    now = _CREATED + _LIFETIME
    _freeze(monkeypatch, now)
    agent = _expired_agent()

    reactivated = reactivate_agent(agent, _host(now))

    assert reactivated.status == "active"
    assert reactivated.activated_at == now
    assert reactivated.last_used_at == now
    assert reactivated.created_at == _CREATED
    assert agent.status == "expired"


def test_reactivate_agent_refuses_one_microsecond_past_absolute_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past absolute lifetime cannot be reactivated."""
    now = _CREATED + _LIFETIME + timedelta(microseconds=1)
    _freeze(monkeypatch, now)
    agent = _expired_agent()

    with pytest.raises(ValueError, match="absolute lifetime"):
        reactivate_agent(agent, _host(now))

    assert agent.status == "expired"
    assert agent.activated_at == _CREATED
    assert agent.last_used_at is None
