"""Lock Agent JWT lifetime fences at the exact deadline.

``check_agent_expiry`` uses a strict ``>`` comparison. These tests prove
``verify_agent_jwt`` still slides ``last_used_at`` on that instant and rejects
one microsecond later without sliding. Well-past cases do not pin this fence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.auth.agent_jwt import JwtVerifyResult, create_agent_jwt, verify_agent_jwt
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from tests.crypto.jwk_helpers import ed25519_public_jwk

_FENCE = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
_ONE_MICROSECOND = timedelta(microseconds=1)

pytestmark = pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")


class _FrozenDateTime(datetime):
    """Session slide calls ``datetime.now``; expiry uses ``_utc_now``."""

    current: datetime = _FENCE

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def _freeze_lifetime_clock(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    """Freeze expiry and the slide timestamp to the same instant.

    Slide writes ``agent_jwt_session.datetime.now``, not ``_utc_now``. Leaving
    that on the wall clock would hide whether the deadline itself was accepted.
    """
    _FrozenDateTime.current = instant

    def _utc_now() -> datetime:
        return instant

    monkeypatch.setattr("asap.auth.identity._utc_now", _utc_now)
    monkeypatch.setattr("asap.auth.agent_jwt_session.datetime", _FrozenDateTime)


def _fence_agent(
    public_key: dict[str, str],
    created_at: datetime,
    *,
    session_ttl: timedelta | None,
    last_used_at: datetime | None,
    absolute_lifetime: timedelta | None,
    max_lifetime: timedelta | None,
    activated_at: datetime | None,
) -> AgentSession:
    return AgentSession(
        agent_id="agent-fence",
        host_id="host-fence",
        public_key=public_key,
        mode="delegated",
        status="active",
        created_at=created_at,
        session_ttl=session_ttl,
        last_used_at=last_used_at,
        absolute_lifetime=absolute_lifetime,
        max_lifetime=max_lifetime,
        activated_at=activated_at,
    )


async def _save_fence_pair(
    host_public: dict[str, str],
    agent_public: dict[str, str],
    created_at: datetime,
    *,
    session_ttl: timedelta | None,
    last_used_at: datetime | None,
    absolute_lifetime: timedelta | None,
    max_lifetime: timedelta | None,
    activated_at: datetime | None,
) -> tuple[InMemoryHostStore, InMemoryAgentStore]:
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id="host-fence",
            public_key=host_public,
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await agents.save(
        _fence_agent(
            agent_public,
            created_at,
            session_ttl=session_ttl,
            last_used_at=last_used_at,
            absolute_lifetime=absolute_lifetime,
            max_lifetime=max_lifetime,
            activated_at=activated_at,
        )
    )
    return hosts, agents


async def _verify_at_instant(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime,
    created_at: datetime,
    session_ttl: timedelta | None = None,
    last_used_at: datetime | None = None,
    absolute_lifetime: timedelta | None = None,
    max_lifetime: timedelta | None = None,
    activated_at: datetime | None = None,
) -> tuple[JwtVerifyResult, AgentSession]:
    _freeze_lifetime_clock(monkeypatch, now)
    host_key = Ed25519PrivateKey.generate()
    agent_key = Ed25519PrivateKey.generate()
    host_public = ed25519_public_jwk(host_key)
    hosts, agents = await _save_fence_pair(
        host_public,
        ed25519_public_jwk(agent_key),
        created_at,
        session_ttl=session_ttl,
        last_used_at=last_used_at,
        absolute_lifetime=absolute_lifetime,
        max_lifetime=max_lifetime,
        activated_at=activated_at,
    )
    token = create_agent_jwt(
        agent_key,
        host_thumbprint=jwk_thumbprint_sha256(host_public),
        agent_id="agent-fence",
        aud="asap-fence",
    )
    result = await verify_agent_jwt(token, hosts, agents)
    stored = await agents.get("agent-fence")
    assert stored is not None
    return result, stored


def _assert_slid(result: JwtVerifyResult, stored: AgentSession) -> None:
    assert result.ok is True
    assert result.error is None
    assert result.agent is not None
    assert result.agent.last_used_at == _FENCE
    assert stored.last_used_at == _FENCE
    assert stored.status == "active"


def _assert_rejected(
    result: JwtVerifyResult,
    stored: AgentSession,
    *,
    error: str,
    last_used_at: datetime | None,
) -> None:
    assert result.ok is False
    assert result.error == error
    assert result.agent is None
    assert stored.status == "active"
    assert stored.last_used_at == last_used_at


async def test_verify_agent_jwt_slides_at_absolute_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The absolute-lifetime instant is still authenticable and slides last_used_at."""
    lifetime = timedelta(days=1)
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE,
        created_at=_FENCE - lifetime,
        absolute_lifetime=lifetime,
    )
    _assert_slid(result, stored)


async def test_verify_agent_jwt_revokes_one_microsecond_past_absolute_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past absolute lifetime is agent_revoked and does not slide."""
    lifetime = timedelta(days=1)
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE + _ONE_MICROSECOND,
        created_at=_FENCE - lifetime,
        absolute_lifetime=lifetime,
    )
    _assert_rejected(result, stored, error="agent_revoked", last_used_at=None)


async def test_verify_agent_jwt_slides_at_max_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Max lifetime uses the same strict ``>`` comparison and still slides."""
    lifetime = timedelta(hours=2)
    activated = _FENCE - lifetime
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE,
        created_at=activated,
        max_lifetime=lifetime,
        activated_at=activated,
    )
    _assert_slid(result, stored)


async def test_verify_agent_jwt_expires_one_microsecond_past_max_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past max lifetime is agent_expired and does not slide."""
    lifetime = timedelta(hours=2)
    activated = _FENCE - lifetime
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE + _ONE_MICROSECOND,
        created_at=activated,
        max_lifetime=lifetime,
        activated_at=activated,
    )
    _assert_rejected(result, stored, error="agent_expired", last_used_at=None)


async def test_verify_agent_jwt_slides_at_idle_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idle deadline is still authenticable and refreshes last_used_at."""
    ttl = timedelta(minutes=30)
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE,
        created_at=_FENCE - ttl,
        session_ttl=ttl,
        last_used_at=_FENCE - ttl,
    )
    _assert_slid(result, stored)


async def test_verify_agent_jwt_expires_one_microsecond_past_idle_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past the idle TTL is agent_expired and does not slide."""
    ttl = timedelta(minutes=30)
    last_used = _FENCE - ttl
    result, stored = await _verify_at_instant(
        monkeypatch,
        now=_FENCE + _ONE_MICROSECOND,
        created_at=last_used,
        session_ttl=ttl,
        last_used_at=last_used,
    )
    _assert_rejected(result, stored, error="agent_expired", last_used_at=last_used)
