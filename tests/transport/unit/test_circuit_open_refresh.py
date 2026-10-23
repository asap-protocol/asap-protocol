"""Failures while the circuit is OPEN keep it open and refresh the cooldown.

A later failure that does not move ``_last_failure_time`` lets ``can_attempt``
enter HALF_OPEN on the original open timestamp.
"""

from __future__ import annotations

import pytest

from asap.transport.circuit_breaker import CircuitBreaker, CircuitState


class _Clock:
    """Mutable stand-in for ``time.time``."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        """Return the frozen instant."""
        return self.now


def test_failure_while_open_refreshes_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure 50s after open keeps the circuit closed until 60s after that failure."""
    clock = _Clock(1_000.0)
    monkeypatch.setattr("asap.transport.circuit_breaker.time.time", clock.time)
    breaker = CircuitBreaker(threshold=2, timeout=60.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.get_state() == CircuitState.OPEN

    clock.now = 1_050.0
    breaker.record_failure()
    assert breaker.get_state() == CircuitState.OPEN
    assert breaker.get_consecutive_failures() == 3
    assert breaker.can_attempt() is False

    clock.now = 1_109.0
    assert breaker.can_attempt() is False
    assert breaker.get_state() == CircuitState.OPEN

    clock.now = 1_110.0
    assert breaker.can_attempt() is True
    assert breaker.get_state() == CircuitState.HALF_OPEN
