"""Lock envelope replay fences at the configured age and future-tolerance limits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from asap.errors import InvalidTimestampError
from asap.models.constants import MAX_ENVELOPE_AGE_SECONDS, MAX_FUTURE_TOLERANCE_SECONDS
from asap.models.envelope import Envelope
from asap.transport.validators import validate_envelope_timestamp

_FIXED_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """``datetime.now`` stand-in so age math does not depend on the wall clock."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """Return the fixed UTC instant, converted when a timezone is requested."""
        if tz is None:
            return _FIXED_NOW.replace(tzinfo=None)
        return _FIXED_NOW.astimezone(tz)


def _freeze_validators_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``validate_envelope_timestamp`` to ``_FIXED_NOW``."""
    monkeypatch.setattr("asap.transport.validators.datetime", _FrozenDatetime)


def _envelope(timestamp: datetime) -> Envelope:
    """Build a task-request envelope with an explicit timestamp."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:test",
        recipient="urn:asap:agent:target",
        payload_type="TaskRequest",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        timestamp=timestamp,
    )


def test_age_and_future_offset_at_the_limit_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Age of exactly 300s and a future offset of exactly 30s still pass."""
    _freeze_validators_now(monkeypatch)
    aged = _envelope(_FIXED_NOW - timedelta(seconds=MAX_ENVELOPE_AGE_SECONDS))
    future = _envelope(_FIXED_NOW + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS))
    validate_envelope_timestamp(aged)
    validate_envelope_timestamp(future)


def test_age_one_microsecond_past_max_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past the max age is a replay and must be rejected."""
    _freeze_validators_now(monkeypatch)
    stale = _envelope(
        _FIXED_NOW - timedelta(seconds=MAX_ENVELOPE_AGE_SECONDS, microseconds=1),
    )
    with pytest.raises(InvalidTimestampError) as exc_info:
        validate_envelope_timestamp(stale)
    assert exc_info.value.age_seconds is not None
    assert exc_info.value.age_seconds > MAX_ENVELOPE_AGE_SECONDS
    assert "too old" in exc_info.value.message.lower()


def test_future_offset_one_microsecond_past_tolerance_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One microsecond past the future tolerance is rejected."""
    _freeze_validators_now(monkeypatch)
    ahead = _envelope(
        _FIXED_NOW + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS, microseconds=1),
    )
    with pytest.raises(InvalidTimestampError) as exc_info:
        validate_envelope_timestamp(ahead)
    assert exc_info.value.future_offset_seconds is not None
    assert exc_info.value.future_offset_seconds > MAX_FUTURE_TOLERANCE_SECONDS
    assert "too far in the future" in exc_info.value.message.lower()
