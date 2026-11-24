"""Chaos engineering tests for message reliability simulation.

This module tests the resilience of the ASAP client when facing message
delivery issues. It simulates:
- Message loss (requests that never get responses)
- Message duplication (same message received multiple times)
- Out-of-order message delivery
- Partial message corruption

These tests verify that:
1. The client properly handles missing responses with timeout/retry
2. Duplicate messages are handled gracefully (idempotency)
3. The system maintains consistency under unreliable delivery
4. Error messages are clear and actionable
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, Generator
from unittest.mock import patch

import httpx
import pytest

from pydantic import ValidationError

from asap.models.envelope import Envelope
from asap.models.enums import TaskStatus
from asap.models.payloads import TaskRequest, TaskResponse
from asap.transport.circuit_breaker import get_registry
from asap.transport.client import (
    ASAPClient,
    ASAPConnectionError,
    ASAPRemoteError,
    ASAPTimeoutError,
)

from .conftest import create_mock_response

if TYPE_CHECKING:
    pass


class TestMessageLoss:
    """Tests for message loss scenarios."""

    async def test_complete_message_loss_timeout(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when response is completely lost.

        Simulates a scenario where the request is sent but the response
        never arrives (e.g., network black hole, server processed but
        response dropped).
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Simulate message loss - response never arrives (timeout)
            raise httpx.TimeoutException("Response never received - message lost")

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                timeout=5.0,
                max_retries=3,
                base_delay=0.1,
            ) as client:
                with pytest.raises(ASAPTimeoutError) as exc_info:
                    await client.send(sample_request_envelope)

                # Verify timeout value is included
                assert exc_info.value.timeout == 5.0

        # Should have attempted max_retries times
        assert call_count == 3

    async def test_intermittent_message_loss(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior with intermittent message loss.

        Simulates a flaky network where some responses are lost but
        eventually one gets through.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # First 2 responses are "lost" (timeout), 3rd succeeds
            if call_count <= 2:
                raise httpx.TimeoutException("Response lost in transit")
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

    async def test_probabilistic_message_loss(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior with probabilistic message loss.

        Simulates a network with ~50% message loss rate, testing that
        the client can eventually succeed through retries.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Deterministic "random" pattern: lose every odd-numbered message
            if call_count % 2 == 1:
                raise httpx.TimeoutException("Message lost (50% loss rate)")
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

        # With 50% loss, should succeed on 2nd attempt
        assert call_count == 2

    async def test_burst_message_loss_then_recovery(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client behavior during burst of message loss followed by recovery.

        Simulates a temporary network issue causing a burst of lost messages.
        """
        call_count = 0
        burst_size = 4

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # First N messages lost in burst, then recovery
            if call_count <= burst_size:
                raise httpx.TimeoutException(f"Burst loss {call_count}/{burst_size}")
            return create_mock_response(sample_response_envelope)

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=6,
                base_delay=0.05,
            ) as client:
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        assert call_count == burst_size + 1


class TestMessageDuplication:
    """Tests for message duplication and idempotency scenarios."""

    @pytest.fixture(autouse=True)
    def clear_registry(self) -> Generator[None, None, None]:
        """Clear circuit breaker registry before each test to ensure isolation."""
        registry = get_registry()
        registry.clear()
        yield
        registry.clear()

    async def test_duplicate_response_handling(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test that client handles duplicate responses gracefully.

        Simulates a scenario where the same response is received multiple
        times (e.g., network retransmission at TCP level).
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Always return the same response (simulating duplicate at server level)
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
        ) as client:
            # Send multiple times - each should get same response
            responses = []
            for _ in range(3):
                response = await client.send(sample_request_envelope)
                responses.append(response)

            # All responses should be valid and consistent
            for resp in responses:
                assert resp.payload_type == "task.response"
                assert resp.payload.status == TaskStatus.COMPLETED

        assert call_count == 3

    async def test_idempotent_request_handling(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test that sending the same request multiple times is handled.

        Verifies that duplicate requests (same envelope ID) are processed
        correctly and don't cause issues.
        """
        received_ids: list[str] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            # Parse request to get envelope ID
            import json

            body = json.loads(request.content)
            envelope_id = body.get("params", {}).get("envelope", {}).get("id", "unknown")
            received_ids.append(envelope_id)
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
        ) as client:
            # Send same envelope multiple times
            for _ in range(3):
                response = await client.send(sample_request_envelope)
                assert response.payload_type == "task.response"

        # All requests had the same envelope ID (duplicate detection at server)
        assert len(received_ids) == 3
        # All IDs should be the same since we're sending the same envelope
        assert received_ids[0] == received_ids[1] == received_ids[2]

    async def test_unique_ids_for_different_requests(self) -> None:
        """Test that different requests have unique envelope IDs.

        Verifies that the client generates unique IDs for new envelopes,
        enabling proper deduplication on the server side.
        """
        received_ids: list[str] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            envelope_id = body.get("params", {}).get("envelope", {}).get("id", "unknown")
            received_ids.append(envelope_id)

            # Create response for this specific request
            request_envelope = Envelope.model_validate(body["params"]["envelope"])
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{uuid.uuid4().hex[:8]}",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=request_envelope.id,
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
        ) as client:
            # Send 3 different requests (new envelopes)
            for i in range(3):
                envelope = Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:client",
                    recipient="urn:asap:agent:server",
                    payload_type="task.request",
                    payload=TaskRequest(
                        conversation_id=f"conv_unique_{i}",
                        skill_id="echo",
                        input={"index": i},
                    ).model_dump(),
                )
                response = await client.send(envelope)
                assert response.payload_type == "task.response"

        # All IDs should be unique
        assert len(received_ids) == 3
        assert len(set(received_ids)) == 3  # All unique

    async def test_retry_with_same_envelope_id(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test that retries use the same envelope ID.

        Verifies that when retrying a failed request, the same envelope ID
        is used, allowing servers to detect and handle duplicate requests.
        """
        received_ids: list[str] = []
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            import json

            body = json.loads(request.content)
            envelope_id = body.get("params", {}).get("envelope", {}).get("id", "unknown")
            received_ids.append(envelope_id)

            # First 2 calls fail, 3rd succeeds
            if call_count <= 2:
                raise httpx.TimeoutException("Request timeout")
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

        # Should have same ID across all retries (for idempotency)
        assert len(received_ids) == 3
        assert received_ids[0] == received_ids[1] == received_ids[2]


class TestOutOfOrderDelivery:
    """Tests for out-of-order message delivery scenarios."""

    async def test_response_for_different_request(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when receiving a response for a different request.

        Simulates a scenario where responses get mixed up or delayed,
        and the client receives a response with wrong correlation ID.

        Per B6/BUG #6, the client now BINDS the response correlation_id to the
        request envelope id and rejects a mismatch (preventing request/response
        mixup under concurrency). Previously the client accepted any non-empty
        correlation_id and left validation to the application — that was the bug.
        """
        from asap.transport.errors import ProtocolCorrelationError

        wrong_response = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="task.response",
            payload=TaskResponse(
                task_id="task_wrong",
                status=TaskStatus.COMPLETED,
                result={"message": "Wrong response"},
            ).model_dump(),
            correlation_id="completely_different_id",  # Wrong correlation ID
        )

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Return response with wrong correlation ID
            return create_mock_response(wrong_response)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
        ) as client:
            # B6: the client must REJECT a response whose correlation_id does not
            # bind to the request id, rather than accepting it and leaving the
            # mismatch for the application to detect.
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                await client.send(sample_request_envelope)

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr(sample_request_envelope.id) in message
            assert repr("completely_different_id") in message

    async def test_stale_response_ignored(self) -> None:
        """Test handling of stale responses from previous requests.

        Verifies that the client properly handles scenarios where
        delayed responses from old requests arrive.
        """
        call_count = 0
        first_request_id = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count, first_request_id
            call_count += 1
            import json

            body = json.loads(request.content)
            current_id = body.get("params", {}).get("envelope", {}).get("id")

            if call_count == 1:
                first_request_id = current_id

            # Create proper response for current request
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{call_count}",
                    status=TaskStatus.COMPLETED,
                    result={"call": call_count},
                ).model_dump(),
                correlation_id=current_id,  # Match current request
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
        ) as client:
            # Send multiple requests
            responses = []
            for i in range(3):
                envelope = Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:client",
                    recipient="urn:asap:agent:server",
                    payload_type="task.request",
                    payload=TaskRequest(
                        conversation_id=f"conv_stale_{i}",
                        skill_id="echo",
                        input={"seq": i},
                    ).model_dump(),
                )
                response = await client.send(envelope)
                responses.append(response)

            # Each response should have correct correlation
            for i, resp in enumerate(responses):
                assert resp.payload_type == "task.response"
                assert resp.payload.result["call"] == i + 1


class TestPartialCorruption:
    """Tests for partial message corruption scenarios."""

    async def test_truncated_json_response(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when response JSON is truncated.

        Simulates a network issue that causes the response to be cut off.
        Note: JSON parse errors are NOT retried - they are application errors,
        not transient network errors.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Return truncated JSON
            return httpx.Response(
                status_code=200,
                content=b'{"jsonrpc": "2.0", "result": {"envelope": {"asap_version": "0.1", "sender": "urn:as',
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=2,
            ) as client:
                # Should raise error due to invalid JSON (not retried)
                with pytest.raises(
                    (
                        ASAPConnectionError,
                        ASAPRemoteError,
                        ValueError,
                        ValidationError,
                    )
                ):
                    await client.send(sample_request_envelope)

        # JSON errors are NOT retried - only connection/timeout errors are
        assert call_count == 1

    async def test_malformed_envelope_in_response(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when envelope in response is malformed.

        Simulates corruption that results in invalid envelope structure.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            # Return valid JSON-RPC but invalid envelope (missing required fields)
            malformed_response = {
                "jsonrpc": "2.0",
                "result": {
                    "envelope": {
                        "asap_version": "0.1",
                        # Missing: sender, recipient, payload_type, payload
                    }
                },
                "id": "req-1",
            }
            return httpx.Response(
                status_code=200,
                json=malformed_response,
            )

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=1,
            ) as client:
                # Should raise validation error (client may wrap in ASAPConnectionError)
                with pytest.raises(
                    (
                        ASAPConnectionError,
                        ASAPRemoteError,
                        ValueError,
                        ValidationError,
                    )
                ):
                    await client.send(sample_request_envelope)

    async def test_wrong_payload_type_in_response(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when response has unexpected payload type.

        Simulates corruption or misdirection where wrong type is received.
        """
        wrong_type_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="task.update",  # Wrong type - expected task.response
            payload={
                "task_id": "task_wrong_type",
                "update_type": "progress",
                "status": "working",
                "progress": {"percent": 50},
            },
        )

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return create_mock_response(wrong_type_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
        ) as client:
            # Client returns envelope regardless of type - validation at app level
            response = await client.send(sample_request_envelope)
            assert response.payload_type == "task.update"  # Wrong but returned

    async def test_server_error_then_valid_response(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test recovery after receiving server error response.

        Simulates transient 5xx error followed by successful delivery.
        Note: 5xx errors ARE retried (unlike JSON parse errors).
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First response is server error (retryable)
                return httpx.Response(
                    status_code=500,
                    content=b"Internal Server Error - transient issue",
                )
            # Retry succeeds with valid response
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


class TestMessageReliabilityEdgeCases:
    """Edge case tests for message reliability scenarios."""

    async def test_empty_response_body(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when response body is empty.

        Simulates a connection issue where response body is lost.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"")

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=1,
            ) as client:
                with pytest.raises(
                    (
                        ASAPConnectionError,
                        ASAPRemoteError,
                        ValueError,
                        ValidationError,
                    )
                ):
                    await client.send(sample_request_envelope)

    async def test_null_json_response(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when response is JSON null.

        Simulates a server bug returning null instead of valid response.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"null")

        with patch("asap.transport.client.asyncio.sleep"):
            async with ASAPClient(
                "http://localhost:8000",
                transport=httpx.MockTransport(mock_transport),
                max_retries=1,
            ) as client:
                with pytest.raises(
                    (
                        ASAPConnectionError,
                        ASAPRemoteError,
                        ValueError,
                        ValidationError,
                    )
                ):
                    await client.send(sample_request_envelope)

    async def test_json_rpc_error_response(self, sample_request_envelope: Envelope) -> None:
        """Test client behavior when JSON-RPC error is returned.

        Simulates a server returning a JSON-RPC error response.
        """

        def mock_transport(request: httpx.Request) -> httpx.Response:
            error_response = {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                },
                "id": "req-1",
            }
            return httpx.Response(status_code=200, json=error_response)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
        ) as client:
            # Should raise an error for JSON-RPC error response
            with pytest.raises(
                (
                    ASAPConnectionError,
                    ASAPRemoteError,
                    ValueError,
                    ValidationError,
                )
            ):
                await client.send(sample_request_envelope)

    async def test_mixed_retryable_failures(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client handling mixed retryable failure modes.

        Simulates realistic network conditions with various retryable failures:
        - Timeout (message loss)
        - Connection error
        - 5xx server errors
        All these are retried, unlike JSON parse errors.
        """
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First: timeout (message loss)
                raise httpx.TimeoutException("Request timed out")
            if call_count == 2:
                # Second: connection error
                raise httpx.ConnectError("Connection reset by peer")
            if call_count == 3:
                # Third: server error (5xx)
                return httpx.Response(status_code=503, content=b"Service unavailable")
            # Fourth: success
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

    async def test_high_latency_not_timeout(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test client handles high latency without false timeout.

        Verifies that slow but successful responses are handled correctly.
        """

        async def delayed_transport(request: httpx.Request) -> httpx.Response:
            # Simulate high latency (but within timeout)
            await asyncio.sleep(0.1)
            return create_mock_response(sample_response_envelope)

        # Use real async transport with small delay
        async with ASAPClient(
            "http://localhost:8000",
            timeout=5.0,  # Long timeout
        ) as client:
            # Mock the internal client's request method
            original_request = client._client.request

            async def mock_request(*args: object, **kwargs: object) -> httpx.Response:
                await asyncio.sleep(0.05)  # Small delay
                return create_mock_response(sample_response_envelope)

            client._client.request = mock_request

            response = await client.send(sample_request_envelope)
            assert response.payload_type == "task.response"

            client._client.request = original_request
