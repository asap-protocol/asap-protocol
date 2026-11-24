"""Chaos engineering tests for server crash simulation.

This module tests the resilience of the ASAP client when servers crash
unexpectedly during request processing. It simulates:
- Server dying mid-request
- Intermittent server crashes
- Server restart scenarios
- Partial response failures

These tests verify that:
1. The client properly handles aborted connections
2. Retry logic correctly handles server crashes
3. Circuit breakers protect against cascading failures
4. Error messages provide useful debugging information
"""

import asyncio
from typing import TYPE_CHECKING, Generator
from unittest.mock import patch

import httpx
import pytest

from asap.errors import CircuitOpenError
from asap.models.envelope import Envelope
from asap.models.enums import TaskStatus
from asap.models.payloads import TaskResponse
from asap.transport.circuit_breaker import CircuitState, get_registry
from asap.transport.client import (
    ASAPClient,
    ASAPConnectionError,
    ASAPRemoteError,
    RetryConfig,
)

from .conftest import create_mock_response

if TYPE_CHECKING:
    pass


class TestServerCrashBasic:
    """Tests for basic server crash scenarios."""

    async def test_server_crash_during_request(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when server crashes during request.

        Simulates a scenario where the server process dies while processing
        a request, causing the connection to be abruptly terminated.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Simulate server crash - connection reset
            raise httpx.ConnectError("Connection reset by peer - server crashed")

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=3,
                base_delay=0.1,
            ) as client:
                with pytest.raises(ASAPConnectionError) as exc_info:
                    await client.send(sample_request_envelope)

                # Error should indicate connection issue
                assert "Connection" in str(exc_info.value) or "reset" in str(exc_info.value).lower()

        # Should have attempted max_retries times
        assert call_count == 3

    async def test_server_crash_with_http_503(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when server returns 503 (service unavailable).

        This simulates a server that is shutting down or restarting and
        returns a 503 status code.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Server is shutting down
            return httpx.Response(
                status_code=503,
                content=b"Service Unavailable - Server is restarting",
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=3,
                base_delay=0.1,
            ) as client:
                with pytest.raises(ASAPConnectionError) as exc_info:
                    await client.send(sample_request_envelope)

                assert "503" in str(exc_info.value) or "Service" in str(exc_info.value)

        # Should have retried
        assert call_count == 3

    async def test_server_recovers_after_crash(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior when server crashes then recovers.

        Simulates a server that crashes but comes back online within
        the retry window.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # First 2 attempts: server is down
            if call_count <= 2:
                raise httpx.ConnectError("Server crashed")
            # 3rd attempt: server is back
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=5,
                base_delay=0.1,
            ) as client:
                response = await client.send(sample_request_envelope)

                assert response.payload_type == "task.response"

        # Should have succeeded on 3rd attempt
        assert call_count == 3

    async def test_server_crash_502_bad_gateway(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior with 502 Bad Gateway (proxy/load balancer issue).

        This simulates a scenario where the server behind a load balancer
        crashes, causing the LB to return 502.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                status_code=502,
                content=b"Bad Gateway - upstream server crashed",
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=2,
                base_delay=0.1,
            ) as client:
                with pytest.raises(ASAPConnectionError) as exc_info:
                    await client.send(sample_request_envelope)

                assert "502" in str(exc_info.value) or "Gateway" in str(exc_info.value)

        assert call_count == 2


class TestServerCrashWithCircuitBreaker:
    """Tests for server crash scenarios with circuit breaker enabled."""

    @pytest.fixture(autouse=True)
    def clear_registry(self) -> Generator[None, None, None]:
        """Clear circuit breaker registry before each test to ensure isolation."""
        registry = get_registry()
        registry.clear()
        yield
        registry.clear()

    async def test_circuit_opens_on_repeated_crashes(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test circuit breaker opens after repeated server crashes.

        Simulates a server that keeps crashing, triggering the circuit
        breaker to open and fail fast.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=503,
                content=b"Server crashed",
            )

        with patch("asap.transport.client.asyncio.sleep"):
            retry_config = RetryConfig(
                max_retries=1,
                circuit_breaker_enabled=True,
                circuit_breaker_threshold=3,
            )

            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                retry_config=retry_config,
            ) as client:
                # Trigger circuit breaker with crashes
                for _ in range(3):
                    with pytest.raises(ASAPConnectionError):
                        await client.send(sample_request_envelope)

                # Circuit should be open
                assert client._circuit_breaker is not None
                assert client._circuit_breaker.get_state() == CircuitState.OPEN

                # Next request should fail immediately
                with pytest.raises(CircuitOpenError):
                    await client.send(sample_request_envelope)

    async def test_circuit_closes_after_server_restart(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test circuit breaker closes after server successfully restarts.

        Simulates a server that crashes, gets restarted, and the circuit
        breaker successfully transitions back to CLOSED state.
        """
        server_up = False

        def mock_transport(request: httpx.Request) -> httpx.Response:
            if server_up:
                return create_mock_response(sample_response_envelope)
            return httpx.Response(status_code=503, content=b"Server crashed")

        retry_config = RetryConfig(
            max_retries=1,
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=3,
            circuit_breaker_timeout=0.05,  # Short timeout for testing
        )

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            retry_config=retry_config,
        ) as client:
            # Trigger circuit breaker
            for _ in range(3):
                with pytest.raises(ASAPConnectionError):
                    await client.send(sample_request_envelope)

            assert client._circuit_breaker is not None
            assert client._circuit_breaker.get_state() == CircuitState.OPEN

            # Wait for timeout
            await asyncio.sleep(0.1)

            # Simulate server restart
            server_up = True

            # Should recover
            response = await client.send(sample_request_envelope)
            assert response.payload_type == "task.response"
            assert client._circuit_breaker.get_state() == CircuitState.CLOSED


class TestServerCrashPatterns:
    """Tests for specific server crash patterns."""

    async def test_rolling_restart_scenario(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior during rolling restart (K8s style).

        Simulates a scenario where servers are being restarted one by one,
        causing intermittent 503 errors.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Simulate rolling restart: every 3rd request goes to restarting server
            if call_count % 3 == 0:
                return httpx.Response(status_code=503, content=b"Server restarting")
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=3,
            ) as client:
                # All requests should eventually succeed due to retries
                for _ in range(3):
                    response = await client.send(sample_request_envelope)
                    assert response.payload_type == "task.response"

    async def test_oom_kill_pattern(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior when server is OOM killed.

        Simulates a server that gets killed by the OS due to memory pressure,
        causing connection to drop suddenly.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: server dies from OOM
                raise httpx.ConnectError("Connection reset - process killed")
            # Subsequent calls: new server instance is up
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=3,
            ) as client:
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        assert call_count == 2

    async def test_graceful_shutdown_503(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior during graceful server shutdown.

        Simulates a server that is shutting down gracefully and returns
        503 with Retry-After header.
        """
        call_count = 0
        delays: list[float] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Graceful shutdown with retry-after
                return httpx.Response(
                    status_code=503,
                    content=b"Server shutting down",
                    headers={"Retry-After": "1"},
                )
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep") as mock_sleep:

            async def sleep_capture(delay: float) -> None:
                delays.append(delay)

            mock_sleep.side_effect = sleep_capture

            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=3,
            ) as client:
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        assert call_count == 2

    async def test_cascading_failure_multiple_503s(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior during cascading failures.

        Simulates a scenario where multiple backend servers fail in sequence,
        causing a cascade of 503 errors before recovery.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # First 3 requests hit failing backends, 4th succeeds
            if call_count <= 3:
                return httpx.Response(
                    status_code=503,
                    content=f"Backend {call_count} is down".encode(),
                )
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=5,
                base_delay=0.05,
            ) as client:
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        assert call_count == 4


class TestServerCrashEdgeCases:
    """Edge case tests for server crash scenarios."""

    async def test_incomplete_response_before_crash(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test client behavior when server crashes mid-response.

        Simulates a server that starts sending a response but crashes
        before completing it.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            # Simulate incomplete/malformed response due to crash
            return httpx.Response(
                status_code=200,
                content=b'{"jsonrpc": "2.0", "result": {"envelo',  # Truncated JSON
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=1,
            ) as client:
                # Should raise an error due to invalid JSON
                with pytest.raises((ASAPConnectionError, ASAPRemoteError)):
                    await client.send(sample_request_envelope)

    async def test_504_gateway_timeout(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior with 504 Gateway Timeout.

        Simulates a scenario where the server takes too long to respond
        and the load balancer times out.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                status_code=504,
                content=b"Gateway Timeout - server too slow",
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=2,
            ) as client:
                with pytest.raises(ASAPConnectionError) as exc_info:
                    await client.send(sample_request_envelope)

                assert "504" in str(exc_info.value) or "Timeout" in str(exc_info.value)

        assert call_count == 2

    async def test_server_crash_then_503_then_success(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test client handling mixed failure types before success.

        Simulates a progression of different failure types before recovery.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Connection error (crash)
                raise httpx.ConnectError("Server crashed")
            if call_count == 2:
                # 503 (restarting)
                return httpx.Response(status_code=503, content=b"Restarting")
            # Success
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_mixed",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=sample_request_envelope.id,
            )
            return create_mock_response(response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=5,
                base_delay=0.05,
            ) as client:
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        assert call_count == 3
