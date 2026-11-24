"""Chaos tests for WebSocket transport stability.

Covers reconnection, circuit breaker integration, exponential backoff,
graceful shutdown, and CB thread safety.
"""

import asyncio
import json
import threading
import time
from contextlib import suppress
from typing import Any
from unittest.mock import patch

import pytest
import websockets

from asap.models.envelope import Envelope
from asap.transport.circuit_breaker import CircuitBreaker, CircuitState
from asap.transport.ws import (
    WebSocketTransport,
    _reconnect_delay,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_circuit_breaker_registry() -> Any:
    """Clear circuit breaker registry before and after test."""
    from asap.transport.circuit_breaker import _registry

    _registry.clear()
    yield _registry
    _registry.clear()


# ---------------------------------------------------------------------------
# Helpers: Mock WebSocket objects
# ---------------------------------------------------------------------------


class _SilentWebSocket:
    """Mock WS that accepts sends but blocks forever on recv."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        # Block indefinitely until cancelled
        await asyncio.sleep(999)
        return ""  # pragma: no cover

    async def close(self) -> None:
        self._closed = True


class _DroppingWebSocket:
    """Mock WS that raises ConnectionClosed immediately on recv."""

    async def send(self, data: str) -> None:
        pass

    async def recv(self) -> str:
        raise websockets.ConnectionClosed(None, None)

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Existing tests (migrated and cleaned up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_high_latency_resilience() -> None:
    """Client handles high latency (within timeout) gracefully."""
    envelope = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:me",
        recipient="urn:asap:agent:remote",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
    )
    # Per ASAP protocol semantics, the response correlation_id MUST echo the
    # REQUEST envelope's id (see handlers.py + testing/mocks.py). The client
    # binding (B6/BUG #6) enforces this, so the mock must match.
    rpc_response = {
        "jsonrpc": "2.0",
        "result": {
            "envelope": {
                "id": "env_1",
                "asap_version": "0.1",
                "payload_type": "task.response",
                "sender": "urn:asap:agent:remote",
                "recipient": "urn:asap:agent:local",
                "payload": {"task_id": "t1", "status": "completed"},
                "correlation_id": envelope.id,
            }
        },
        "id": "req_1",
    }
    rpc_json = json.dumps(rpc_response)

    class MockLatencyWebSocket:
        """Mock WS with 500ms recv latency."""

        def __init__(self) -> None:
            self.sent_count = 0

        async def send(self, data: str) -> None:
            pass

        async def recv(self) -> str:
            await asyncio.sleep(0.5)  # 500ms latency
            return rpc_json

        async def close(self) -> None:
            pass

    transport = WebSocketTransport(receive_timeout=2.0)
    transport._ws = MockLatencyWebSocket()
    transport._next_request_id = lambda: "req_1"
    transport._recv_task = asyncio.create_task(transport._recv_loop())

    try:
        start_time = time.monotonic()
        result = await transport.send_and_receive(envelope)
        duration = time.monotonic() - start_time

        assert result.payload_type == "task.response"
        assert duration >= 0.4  # allow small timing variance
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_websocket_circuit_breaker_class_integration(
    clean_circuit_breaker_registry: dict[str, CircuitBreaker],
) -> None:
    """Circuit breaker opens after threshold and blocks further attempts."""
    cb = clean_circuit_breaker_registry.get_or_create("ws://bad-host", threshold=2)

    transport = WebSocketTransport(circuit_breaker=cb, reconnect_on_disconnect=False)

    original_connect = websockets.connect

    async def mock_fail_connect(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Connection refused")

    websockets.connect = mock_fail_connect

    try:
        with pytest.raises(OSError):
            await transport.connect("ws://bad-host")

        # Manually trip the CB (transport doesn't guard connect() today)
        assert cb.get_state() == CircuitState.CLOSED
        cb.record_failure()
        assert cb.get_state() == CircuitState.CLOSED
        cb.record_failure()
        assert cb.get_state() == CircuitState.OPEN
        assert not cb.can_attempt()
    finally:
        websockets.connect = original_connect
        await transport.close()


@pytest.mark.asyncio
async def test_exponential_backoff_timing() -> None:
    """Verify backoff calculation follows 1, 2, 4 pattern capped at max."""
    cases = [
        (1, 1.0, 30.0, 1.0),
        (2, 1.0, 30.0, 2.0),
        (3, 1.0, 30.0, 4.0),
        (4, 1.0, 30.0, 8.0),
        (5, 1.0, 30.0, 16.0),
        (6, 1.0, 30.0, 30.0),  # Cap check
    ]

    for attempt, initial, max_b, expected in cases:
        delay = _reconnect_delay(attempt, initial, max_b)
        assert delay == expected, f"Attempt {attempt}: expected {expected}, got {delay}"


# ---------------------------------------------------------------------------
# 3.1 — Reconnection after forced disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnection_after_forced_disconnect() -> None:
    """Transport reconnects when server drops the connection.

    First connect returns DroppingWebSocket → _run_loop enters reconnection →
    second connect succeeds with StableWebSocket.
    """
    connect_call_count = 0
    connected_event = asyncio.Event()

    class _StableWebSocket:
        """Mock WS that stays alive until cancelled."""

        async def send(self, data: str) -> None:
            pass

        async def recv(self) -> str:
            await asyncio.sleep(999)
            return ""  # pragma: no cover

        async def close(self) -> None:
            pass

    async def mock_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal connect_call_count
        connect_call_count += 1
        if connect_call_count == 1:
            # First connection: will be "dropped" by _DroppingWebSocket
            return _DroppingWebSocket()
        # Subsequent connections: stable
        connected_event.set()
        return _StableWebSocket()

    transport = WebSocketTransport(
        reconnect_on_disconnect=True,
        max_reconnect_attempts=5,
        initial_backoff=0.05,  # Fast backoff for tests
        max_backoff=0.1,
        ping_interval=None,  # Disable ping to avoid noise
    )

    with patch("websockets.connect", side_effect=mock_connect):
        await transport.connect("ws://flaky-server")

        # Wait for reconnection to happen (second connect call)
        await asyncio.wait_for(connected_event.wait(), timeout=5.0)

        assert connect_call_count >= 2, (
            f"Expected at least 2 connect calls (initial + reconnect), got {connect_call_count}"
        )
        # Transport should be connected after recovery
        assert transport._ws is not None

    await transport.close()

    # After close, all tasks should be cleaned up
    assert transport._run_task is None
    assert transport._recv_task is None
    assert transport._ack_check_task is None
    assert transport._ws is None


# ---------------------------------------------------------------------------
# 3.2 — Circuit breaker opens via ack timeout (integrated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_ack_timeout(
    clean_circuit_breaker_registry: dict[str, CircuitBreaker],
) -> None:
    """CB opens when acks time out via _ack_check_loop (real integration path)."""
    cb = CircuitBreaker(threshold=2, timeout=60.0)

    transport = WebSocketTransport(
        circuit_breaker=cb,
        ack_timeout_seconds=0.05,  # 50ms timeout — fast for testing
        max_ack_retries=0,  # No retransmissions: fail immediately
        ack_check_interval=0.03,  # Check every 30ms
    )

    mock_ws = _SilentWebSocket()
    transport._ws = mock_ws
    transport._ack_check_task = asyncio.create_task(transport._ack_check_loop())

    # Send 2 envelopes that require ack — since max_ack_retries=0,
    # each one that times out will record ONE failure on the CB.
    # With threshold=2, after 2 failures the CB should open.
    env1 = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
    )
    env2 = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c2", "skill_id": "s1", "input": {}},
    )

    # Register pending acks manually (simulating what send() does)
    transport._register_pending_ack(env1)
    transport._register_pending_ack(env2)

    assert len(transport._pending_acks) == 2
    assert cb.get_state() == CircuitState.CLOSED

    # Wait enough for the ack_check_loop to detect timeouts:
    # ack_timeout=50ms + ack_check_interval=30ms → ~80ms per cycle
    # We need at least 1 cycle to detect both expired acks.
    await asyncio.sleep(0.3)

    # Both acks should have been removed (expired, no retransmissions)
    assert len(transport._pending_acks) == 0, (
        f"Expected 0 pending acks, got {len(transport._pending_acks)}"
    )

    # CB should have received 2 failures (one per expired ack) → OPEN
    assert cb.get_state() == CircuitState.OPEN, f"Expected CB state OPEN, got {cb.get_state()}"
    assert cb.get_consecutive_failures() == 2

    await transport.close()


# ---------------------------------------------------------------------------
# 3.2b — Circuit breaker with ack retransmission before opening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_retransmission_exhaustion(
    clean_circuit_breaker_registry: dict[str, CircuitBreaker],
) -> None:
    """CB opens only AFTER retransmissions are exhausted, not during retries."""
    cb = CircuitBreaker(threshold=2, timeout=60.0)

    transport = WebSocketTransport(
        circuit_breaker=cb,
        ack_timeout_seconds=0.05,
        max_ack_retries=1,  # 1 retransmission before giving up
        ack_check_interval=0.03,
    )

    mock_ws = _SilentWebSocket()
    transport._ws = mock_ws
    transport._ack_check_task = asyncio.create_task(transport._ack_check_loop())

    env = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
    )
    transport._register_pending_ack(env)

    # After first timeout (~50ms) + ack_check_interval (~30ms): retransmit.
    # The retransmission resets sent_at, so CB should still be CLOSED.
    await asyncio.sleep(0.12)
    # There should have been at least one retransmission (send called)
    assert len(mock_ws.sent) >= 1, f"Expected ≥1 retransmission, got {len(mock_ws.sent)}"

    # The CB threshold is 2, and with only 1 envelope exhausting retries,
    # we get 1 failure → CB stays CLOSED (below threshold)
    # Let the second timeout cycle complete to exhaust retries
    await asyncio.sleep(0.15)
    assert cb.get_consecutive_failures() >= 1
    # With threshold=2 and only 1 envelope, CB should stay CLOSED
    assert cb.get_state() == CircuitState.CLOSED, (
        f"Expected CB CLOSED with 1 failure (threshold=2), got {cb.get_state()}"
    )

    # Now register a second envelope → after timeout, CB gets 2nd failure → OPEN
    env2 = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c2", "skill_id": "s1", "input": {}},
    )
    transport._register_pending_ack(env2)
    # Wait for ack_check_loop: timeout (50ms) + retransmit cycle + 2nd timeout + check
    # ~50ms + 30ms + 50ms + 30ms = 160ms minimum; use 0.35s for CI stability
    await asyncio.sleep(0.35)
    assert cb.get_state() == CircuitState.OPEN, (
        f"Expected CB OPEN after 2 failures, got {cb.get_state()}"
    )

    await transport.close()


# ---------------------------------------------------------------------------
# 3.3 — Graceful shutdown during active reconnection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_shutdown_during_reconnection() -> None:
    """close() during _run_loop backoff cleans up all tasks without leaks.

    First connect returns DroppingWebSocket so _run_loop enters reconnection
    naturally; subsequent connects fail.
    """
    connect_calls = 0

    async def mock_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            # First connect "succeeds" with a WS that drops immediately
            return _DroppingWebSocket()
        # All subsequent connects fail, simulating server down
        raise OSError("Connection refused")

    transport = WebSocketTransport(
        reconnect_on_disconnect=True,
        max_reconnect_attempts=100,  # High limit — we'll close before reaching it
        initial_backoff=0.05,  # Short for tests
        max_backoff=0.1,
        ping_interval=None,
    )

    with patch("websockets.connect", side_effect=mock_connect):
        await transport.connect("ws://unreachable")

        # Let _run_loop detect disconnect and enter backoff sleep
        await asyncio.sleep(0.4)
        assert connect_calls >= 2, f"Expected reconnection attempt, got {connect_calls}"

        # Close while _run_loop is in backoff sleep
        await transport.close()

    # All tasks must be cleaned up — no leaked coroutines
    assert transport._run_task is None
    assert transport._recv_task is None
    assert transport._ack_check_task is None
    assert transport._ws is None


@pytest.mark.asyncio
async def test_graceful_shutdown_cancels_pending_futures() -> None:
    transport = WebSocketTransport()
    mock_ws = _SilentWebSocket()
    transport._ws = mock_ws

    # Create pending futures as if send_and_receive had been called
    loop = asyncio.get_running_loop()
    futures = [loop.create_future() for _ in range(3)]
    for i, f in enumerate(futures):
        transport._pending[f"ws-req-{i}"] = f

    await transport.close()

    # All futures should have been resolved with TimeoutError
    for i, f in enumerate(futures):
        assert f.done(), f"Future {i} should be done after close"
        with pytest.raises(asyncio.TimeoutError):
            f.result()

    assert len(transport._pending) == 0


@pytest.mark.asyncio
async def test_graceful_shutdown_clears_pending_acks() -> None:
    transport = WebSocketTransport()
    mock_ws = _SilentWebSocket()
    transport._ws = mock_ws

    env1 = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
    )
    env2 = Envelope(
        asap_version="0.1",
        payload_type="task.request",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload={"conversation_id": "c2", "skill_id": "s1", "input": {}},
    )
    transport._register_pending_ack(env1)
    transport._register_pending_ack(env2)

    assert len(transport._pending_acks) == 2

    await transport.close()

    assert len(transport._pending_acks) == 0


# ---------------------------------------------------------------------------
# 3.4 — Circuit breaker thread safety
# ---------------------------------------------------------------------------


def test_circuit_breaker_thread_safety() -> None:
    """CB handles concurrent record_failure + can_attempt without data races."""
    cb = CircuitBreaker(threshold=10, timeout=0.1)
    errors: list[Exception] = []
    iterations_per_thread = 100

    def hammer_failure() -> None:
        for _ in range(iterations_per_thread):
            try:
                cb.record_failure()
            except Exception as e:
                errors.append(e)

    def hammer_attempt() -> None:
        for _ in range(iterations_per_thread):
            try:
                cb.can_attempt()
            except Exception as e:
                errors.append(e)

    failure_threads = [threading.Thread(target=hammer_failure) for _ in range(5)]
    attempt_threads = [threading.Thread(target=hammer_attempt) for _ in range(5)]
    all_threads = failure_threads + attempt_threads

    for t in all_threads:
        t.start()
    for t in all_threads:
        t.join()

    assert len(errors) == 0, f"Thread safety violations: {errors}"
    # 5 threads × 100 failures = 500 total
    assert cb.get_consecutive_failures() == 5 * iterations_per_thread
    assert cb.get_state() == CircuitState.OPEN


def test_circuit_breaker_half_open_single_permit() -> None:
    """In HALF_OPEN, only one caller gets True from can_attempt()."""
    cb = CircuitBreaker(threshold=1, timeout=0.05)

    # Trip the breaker
    cb.record_failure()
    assert cb.get_state() == CircuitState.OPEN

    # Wait for timeout to transition to HALF_OPEN
    time.sleep(0.06)

    # First call → True (single permit consumed)
    assert cb.can_attempt() is True
    assert cb.get_state() == CircuitState.HALF_OPEN

    # Second call → False (permit already consumed)
    assert cb.can_attempt() is False

    # Record success → transitions to CLOSED
    cb.record_success()
    assert cb.get_state() == CircuitState.CLOSED
    assert cb.get_consecutive_failures() == 0


def test_circuit_breaker_half_open_failure_reopens() -> None:
    """Failure in HALF_OPEN state immediately reopens the circuit."""
    cb = CircuitBreaker(threshold=1, timeout=0.05)

    cb.record_failure()
    assert cb.get_state() == CircuitState.OPEN

    time.sleep(0.06)

    # One request allowed through (HALF_OPEN)
    assert cb.can_attempt() is True

    # That request fails → back to OPEN
    cb.record_failure()
    assert cb.get_state() == CircuitState.OPEN


def test_circuit_breaker_concurrent_half_open_permits() -> None:
    """Concurrent access in HALF_OPEN: exactly one thread gets the permit."""
    cb = CircuitBreaker(threshold=1, timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)  # transition to HALF_OPEN

    results: list[bool] = []
    lock = threading.Lock()

    def try_attempt() -> None:
        result = cb.can_attempt()
        with lock:
            results.append(result)

    threads = [threading.Thread(target=try_attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should have received True
    assert results.count(True) == 1, f"Expected exactly 1 permit, got {results.count(True)}"


# ---------------------------------------------------------------------------
# 3.5 — Backoff delays increase in _run_loop (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_delays_increase_in_run_loop() -> None:
    """_run_loop applies increasing backoff delays between reconnect attempts.

    First connect returns DroppingWebSocket; patches asyncio.sleep to record delays.
    """
    recorded_delays: list[float] = []
    original_sleep = asyncio.sleep
    connect_calls = 0

    async def tracking_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        recorded_delays.append(delay)
        # Don't actually sleep long
        await original_sleep(0.001)

    async def mock_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            return _DroppingWebSocket()
        raise OSError("Connection refused")

    transport = WebSocketTransport(
        reconnect_on_disconnect=True,
        max_reconnect_attempts=5,
        initial_backoff=1.0,
        max_backoff=10.0,
        ping_interval=None,
    )

    with (
        patch("websockets.connect", side_effect=mock_connect),
        patch("asyncio.sleep", side_effect=tracking_sleep),
    ):
        await transport.connect("ws://bad")

        # Give it time to exhaust all attempts
        await original_sleep(0.3)

        await transport.close()

    # Filter for reconnection delays (backoff values from _reconnect_delay).
    # The first delay may come from _run_loop's successful connect path
    # (closing the ws) which invokes ws.close with the close_timeout.
    # Actual backoff delays follow _reconnect_delay(attempt, 1.0, 10.0)
    # which produces: 1.0, 2.0, 4.0, 8.0, ...
    # We extract only delays matching valid _reconnect_delay output.
    valid_backoff_values = {_reconnect_delay(a, 1.0, 10.0) for a in range(1, 10)}
    backoff_delays = [d for d in recorded_delays if d in valid_backoff_values]

    assert len(backoff_delays) >= 2, (
        f"Expected at least 2 backoff delays, got {len(backoff_delays)}: {backoff_delays}."
        f" All recorded: {recorded_delays}"
    )

    # Verify monotonically non-decreasing (exponential backoff property)
    for i in range(1, len(backoff_delays)):
        assert backoff_delays[i] >= backoff_delays[i - 1], (
            f"Backoff delay decreased at index {i}: "
            f"{backoff_delays[i]} < {backoff_delays[i - 1]}. "
            f"Full sequence: {backoff_delays}"
        )


@pytest.mark.asyncio
async def test_max_reconnect_attempts_respected() -> None:
    """_run_loop stops reconnecting after max_reconnect_attempts."""
    connect_calls = 0

    async def mock_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            return _DroppingWebSocket()
        raise OSError("Connection refused")

    transport = WebSocketTransport(
        reconnect_on_disconnect=True,
        max_reconnect_attempts=3,
        initial_backoff=0.01,
        max_backoff=0.02,
        ping_interval=None,
    )

    original_sleep = asyncio.sleep

    async def fast_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        await original_sleep(0.001)

    with (
        patch("websockets.connect", side_effect=mock_connect),
        patch("asyncio.sleep", side_effect=fast_sleep),
    ):
        await transport.connect("ws://bad")

        # Wait for _run_loop to exhaust all reconnection attempts
        await original_sleep(0.5)

        # _run_loop should have stopped (task completed or about to)
        if transport._run_task is not None:
            with suppress(asyncio.CancelledError, OSError):
                await asyncio.wait_for(transport._run_task, timeout=1.0)

        await transport.close()

    # Should have attempted initial connect + limited reconnects
    assert connect_calls <= 5, (
        f"Expected ≤5 connect calls (1 initial + max_reconnect_attempts=3), got {connect_calls}"
    )
