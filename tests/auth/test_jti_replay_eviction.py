"""Replay-cache eviction and TTL boundaries that auth depends on."""

from __future__ import annotations

import pytest

from asap.auth.agent_jwt import JtiReplayCache
from asap.auth.jti_replay_cache import RedisJtiReplayCache


class _ExRecordingClient:
    """Captures Redis ``SET ... EX`` without a live server."""

    def __init__(self) -> None:
        self.expiry_seconds: list[int] = []

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        """Record the expiry argument and pretend the key was created."""
        _ = (key, value)
        if nx is not True:
            raise AssertionError(f"jti replay SET must pass nx=True, got {nx}")
        if ex is None:
            raise AssertionError("jti replay SET must pass ex")
        self.expiry_seconds.append(ex)
        return True


def test_jti_replay_evicts_soonest_expiry_not_oldest_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-valid jti with the soonest expiry is dropped before a longer-lived one."""
    clock = {"now": 5_000.0}
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: clock["now"])
    cache = JtiReplayCache(ttl_seconds=1_000.0, max_size=2)

    assert cache.check_and_record("host", "later-expiry") is True
    clock["now"] = 4_500.0
    assert cache.check_and_record("host", "sooner-expiry") is True
    clock["now"] = 4_600.0
    assert cache.check_and_record("host", "middle-expiry") is True

    assert cache.contains("host", "sooner-expiry") is False
    assert cache.contains("host", "later-expiry") is True
    assert cache.contains("host", "middle-expiry") is True
    assert cache.check_and_record("host", "later-expiry") is False
    assert cache.check_and_record("host", "sooner-expiry") is True


def test_in_memory_jti_keeps_fractional_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-memory replay keeps fractional seconds and allows reuse when now equals expiry.

    A live entry is ``exp > now``. The nonce store uses the other edge
    (``expiry >= now`` still counts as used).
    """
    clock = {"now": 10_000.0}
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: clock["now"])
    cache = JtiReplayCache(ttl_seconds=1.5)

    assert cache.check_and_record("part", "jti-1") is True
    clock["now"] = 10_001.0
    assert cache.check_and_record("part", "jti-1") is False
    clock["now"] = 10_001.5
    assert cache.check_and_record("part", "jti-1") is True


def test_redis_jti_ex_floors_fractional_ttl_and_never_uses_zero() -> None:
    """Redis EX floors fractional TTL on purpose: 2.9s becomes 2, and 0.4s becomes 1.

    In-memory TTL keeps the float. Redis only stores whole seconds, so the
    floor is shorter above 1s and the 1s minimum is longer below 1s.
    """
    client = _ExRecordingClient()
    floored = RedisJtiReplayCache(client, ttl_seconds=2.9)
    assert floored.check_and_record("host-a", "jti-floor") is True

    minimum = RedisJtiReplayCache(client, ttl_seconds=0.4)
    assert minimum.check_and_record("host-a", "jti-min") is True

    assert client.expiry_seconds == [2, 1]
