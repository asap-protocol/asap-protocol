"""Lock the key-rotation warning on the exact day boundary.

``warn_if_key_old`` warns when ``(now - created_at).days >= max_age_days``.
``timedelta.days`` truncates, so one second short of the limit day is still
the previous day and must stay silent. A key one whole day younger stays
silent too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from unittest.mock import MagicMock

import pytest

from asap.crypto.keys import KEY_ROTATION_WARNING_DAYS, KeyMetadata, warn_if_key_old

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    """``warn_if_key_old`` reads ``datetime.now(timezone.utc)``."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return _NOW.replace(tzinfo=None)
        return _NOW.astimezone(tz)


def _freeze_key_clock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    logger = MagicMock()
    monkeypatch.setattr("asap.crypto.keys.datetime", _FrozenDateTime)
    monkeypatch.setattr("asap.crypto.keys.logger", logger)
    return logger


def test_warn_if_key_old_is_silent_one_day_before_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Day 364 of a 365-day limit must not recommend rotation."""
    logger = _freeze_key_clock(monkeypatch)
    created = _NOW - timedelta(days=KEY_ROTATION_WARNING_DAYS - 1)

    warn_if_key_old(KeyMetadata(created_at=created))

    logger.warning.assert_not_called()


def test_warn_if_key_old_is_silent_one_second_short_of_limit_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One second short of the limit day truncates to the previous day."""
    logger = _freeze_key_clock(monkeypatch)
    created = _NOW - timedelta(days=KEY_ROTATION_WARNING_DAYS) + timedelta(seconds=1)
    age_days = (_NOW - created).days
    if age_days != KEY_ROTATION_WARNING_DAYS - 1:
        raise AssertionError(f"age {age_days!r} is not one day under {KEY_ROTATION_WARNING_DAYS!r}")

    warn_if_key_old(KeyMetadata(created_at=created))

    logger.warning.assert_not_called()


def test_warn_if_key_old_logs_on_the_limit_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit day itself recommends rotation (``>=``, not ``>``)."""
    logger = _freeze_key_clock(monkeypatch)
    created = _NOW - timedelta(days=KEY_ROTATION_WARNING_DAYS)

    warn_if_key_old(KeyMetadata(created_at=created))

    logger.warning.assert_called_once()
    args, kwargs = logger.warning.call_args
    assert args == ("key_rotation_recommended",)
    assert kwargs["age_days"] == KEY_ROTATION_WARNING_DAYS
    assert kwargs["max_age_days"] == KEY_ROTATION_WARNING_DAYS
    assert kwargs["created_at"] == created.isoformat()
