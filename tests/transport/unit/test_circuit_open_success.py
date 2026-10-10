"""Success recorded while OPEN must not skip the cool-down."""

from __future__ import annotations

from unittest.mock import patch

from asap.transport.circuit_breaker import CircuitBreaker, CircuitState


def test_success_while_open_keeps_the_original_cooldown() -> None:
    """Only a HALF_OPEN probe may close the circuit.

    ``record_success`` zeroes the failure counter in every state, but it
    clears the open timer only from HALF_OPEN. A success that arrives while
    the circuit is still OPEN must leave ``can_attempt`` false until the
    original timeout elapses.
    """
    opened_at = 1_700_000_000.0
    breaker = CircuitBreaker(threshold=3, timeout=60.0)
    with patch("asap.transport.circuit_breaker.time.time", return_value=opened_at):
        for _ in range(3):
            breaker.record_failure()
    assert breaker.get_state() == CircuitState.OPEN

    breaker.record_success()
    assert breaker.get_state() == CircuitState.OPEN
    assert breaker.get_consecutive_failures() == 0

    with patch("asap.transport.circuit_breaker.time.time", return_value=opened_at + 59.0):
        assert breaker.can_attempt() is False
        assert breaker.get_state() == CircuitState.OPEN

    with patch("asap.transport.circuit_breaker.time.time", return_value=opened_at + 60.0):
        assert breaker.can_attempt() is True
        assert breaker.get_state() == CircuitState.HALF_OPEN
