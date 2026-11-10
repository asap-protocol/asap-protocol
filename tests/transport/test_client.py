"""Tests for ASAP protocol async HTTP client.

This module tests the ASAPClient class that provides async HTTP
communication between ASAP agents.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from asap.crypto.keys import generate_keypair, public_key_to_base64
from asap.crypto.signing import sign_manifest
from asap.errors import CircuitOpenError, SignatureVerificationError
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.enums import TaskStatus
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse
from asap.testing import assert_envelope_valid, assert_response_correlates
from asap.transport.circuit_breaker import CircuitState, get_registry
from asap.transport.client import (
    ASAPClient,
    ASAPConnectionError,
    ASAPTimeoutError,
    RetryConfig,
)
from asap.transport.errors import ProtocolCorrelationError

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter


# Test fixtures
@pytest.fixture
def sample_request_envelope() -> Envelope:
    """Create a sample request envelope for testing."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv_123",
            skill_id="echo",
            input={"message": "Hello!"},
        ).model_dump(),
    )


@pytest.fixture
def sample_response_envelope(sample_request_envelope: Envelope) -> Envelope:
    """Create a sample response envelope for testing."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:server",
        recipient="urn:asap:agent:client",
        payload_type="task.response",
        payload=TaskResponse(
            task_id="task_456",
            status=TaskStatus.COMPLETED,
            result={"echoed": {"message": "Hello!"}},
        ).model_dump(),
        correlation_id=sample_request_envelope.id,
    )


def create_mock_response(envelope: Envelope, request_id: str | int = "req-1") -> httpx.Response:
    """Create a mock HTTP response with JSON-RPC wrapped envelope."""
    json_rpc_response = {
        "jsonrpc": "2.0",
        "result": {"envelope": envelope.model_dump(mode="json")},
        "id": request_id,
    }
    return httpx.Response(
        status_code=200,
        json=json_rpc_response,
    )


def create_mock_error_response(
    code: int, message: str, request_id: str | int | None = "req-1"
) -> httpx.Response:
    """Create a mock HTTP error response with JSON-RPC error."""
    json_rpc_error = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }
    return httpx.Response(
        status_code=200,
        json=json_rpc_error,
    )


class _CircuitBreakerRegistryCleanup:
    @pytest.fixture(autouse=True)
    def _cleanup_circuit_breaker_registry(self) -> Iterator[None]:
        get_registry().clear()
        yield
        get_registry().clear()


class TestASAPClientContextManager:
    """Tests for ASAPClient as async context manager."""

    async def test_client_as_async_context_manager(self) -> None:
        """Test ASAPClient can be used as async context manager."""
        from asap.transport.client import ASAPClient

        async with ASAPClient("http://localhost:8000") as client:
            assert client is not None

    async def test_client_opens_connection_on_enter(self) -> None:
        """Test client opens HTTP connection on context enter."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")
        assert not client.is_connected

        async with client:
            assert client.is_connected

    async def test_client_closes_connection_on_exit(self) -> None:
        """Test client closes HTTP connection on context exit."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")

        async with client:
            pass  # Connection should be open here

        assert not client.is_connected

    async def test_client_closes_connection_on_exception(self) -> None:
        """Test client closes connection even when exception occurs."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")

        with pytest.raises(RuntimeError):
            async with client:
                raise RuntimeError("Test exception")

        assert not client.is_connected

    async def test_client_with_custom_timeout(self) -> None:
        """Test client accepts custom timeout configuration."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000", timeout=30.0)
        assert client.timeout == 30.0

    async def test_client_creates_async_client_with_pool_limits(self) -> None:
        """Test ASAPClient passes pool config to httpx.AsyncClient as Limits and Timeout."""
        from asap.transport.client import ASAPClient, DEFAULT_POOL_TIMEOUT

        mock_instance = AsyncMock()
        mock_instance.aclose = AsyncMock()
        with patch(
            "asap.transport.client.httpx.AsyncClient", return_value=mock_instance
        ) as mock_async_client:
            client = ASAPClient(
                "http://localhost:8000",
                pool_connections=50,
                pool_maxsize=200,
                pool_timeout=10.0,
            )
            async with client:
                pass
            mock_async_client.assert_called_once()
            call_kwargs = mock_async_client.call_args.kwargs
            assert "limits" in call_kwargs
            limits = call_kwargs["limits"]
            assert isinstance(limits, httpx.Limits)
            assert limits.max_keepalive_connections == 50
            assert limits.max_connections == 200
            assert limits.keepalive_expiry == DEFAULT_POOL_TIMEOUT
            assert "timeout" in call_kwargs
            timeout = call_kwargs["timeout"]
            assert isinstance(timeout, httpx.Timeout)
            assert timeout.pool == 10.0

    async def test_client_http2_enabled_by_default(self) -> None:
        """Test ASAPClient has HTTP/2 enabled by default."""
        from asap.transport.client import ASAPClient

        mock_instance = AsyncMock()
        mock_instance.aclose = AsyncMock()
        with patch(
            "asap.transport.client.httpx.AsyncClient", return_value=mock_instance
        ) as mock_async_client:
            client = ASAPClient("http://localhost:8000")
            async with client:
                pass
            mock_async_client.assert_called_once()
            call_kwargs = mock_async_client.call_args.kwargs
            # HTTP/2 should be enabled by default
            assert call_kwargs.get("http2") is True

    async def test_client_http2_can_be_disabled(self) -> None:
        """Test ASAPClient can disable HTTP/2."""
        from asap.transport.client import ASAPClient

        mock_instance = AsyncMock()
        mock_instance.aclose = AsyncMock()
        with patch(
            "asap.transport.client.httpx.AsyncClient", return_value=mock_instance
        ) as mock_async_client:
            client = ASAPClient("http://localhost:8000", http2=False)
            async with client:
                pass
            mock_async_client.assert_called_once()
            call_kwargs = mock_async_client.call_args.kwargs
            # HTTP/2 should be disabled
            assert call_kwargs.get("http2") is False

    async def test_client_http2_not_passed_with_custom_transport(self) -> None:
        """Test HTTP/2 flag is not passed when using custom transport (for testing)."""
        from asap.transport.client import ASAPClient

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={})

        # Custom transport is used for testing - http2 shouldn't be passed
        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            # Just verify client works with custom transport
            assert client.is_connected

    async def test_client_default_timeout(self) -> None:
        """Test client has reasonable default timeout."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")
        assert client.timeout > 0  # Should have a positive default

    def test_client_rejects_invalid_url_scheme(self) -> None:
        """Test client rejects URLs with invalid schemes."""
        from asap.transport.client import ASAPClient

        invalid_urls = [
            ("ftp://example.com", "Invalid URL scheme"),
            ("javascript://example.com", "Invalid URL scheme"),
            ("data://example.com", "Invalid URL scheme"),
        ]

        for url, expected_match in invalid_urls:
            with pytest.raises(ValueError, match=expected_match):
                ASAPClient(url)

        with pytest.raises(ValueError):
            ASAPClient("file:///path/to/file")

    def test_client_accepts_http_scheme(self) -> None:
        """Test client accepts http:// URLs."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")
        assert client.base_url == "http://localhost:8000"

    def test_client_accepts_https_scheme(self) -> None:
        """Test client accepts https:// URLs."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("https://example.com")
        assert client.base_url == "https://example.com"

    def test_client_rejects_malformed_url(self) -> None:
        """Test client rejects malformed URLs."""
        from asap.transport.client import ASAPClient

        invalid_urls = [
            "not-a-url",
            "://missing-scheme",
            "http://",  # Missing netloc
        ]

        for url in invalid_urls:
            with pytest.raises(ValueError, match="Invalid base_url format"):
                ASAPClient(url)


class TestASAPClientSend:
    """Tests for ASAPClient.send() method."""

    async def test_send_returns_response_envelope(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() returns response envelope from server."""
        from asap.transport.client import ASAPClient

        # Create a mock transport
        def mock_transport(request: httpx.Request) -> httpx.Response:
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            response = await client.send(sample_request_envelope)

        assert_envelope_valid(response, allowed_payload_types=["task.response"])
        assert_response_correlates(sample_request_envelope, response)

    async def test_send_includes_envelope_in_request(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() includes envelope in JSON-RPC request params."""
        from asap.transport.client import ASAPClient

        captured_request: httpx.Request | None = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            await client.send(sample_request_envelope)

        assert captured_request is not None
        body = json.loads(captured_request.content)
        assert body["method"] == "asap.send"
        assert "envelope" in body["params"]

    async def test_send_posts_to_asap_endpoint(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() POSTs to /asap endpoint."""
        from asap.transport.client import ASAPClient

        captured_request: httpx.Request | None = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            await client.send(sample_request_envelope)

        assert captured_request is not None
        assert captured_request.method == "POST"
        assert str(captured_request.url) == "http://localhost:8000/asap"

    async def test_send_sets_content_type_json(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() sets Content-Type to application/json."""
        from asap.transport.client import ASAPClient

        captured_request: httpx.Request | None = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            await client.send(sample_request_envelope)

        assert captured_request is not None
        assert "application/json" in captured_request.headers.get("content-type", "")

    async def test_send_includes_authorization_bearer_when_auth_token_set(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        from asap.transport.client import ASAPClient

        captured_request: httpx.Request | None = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            auth_token="secret-bearer-token",
        ) as client:
            await client.send(sample_request_envelope)

        assert captured_request is not None
        assert captured_request.headers.get("Authorization") == "Bearer secret-bearer-token"


class TestASAPClientErrorHandling:
    """Tests for ASAPClient error handling."""

    async def test_send_raises_on_connection_error(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises ConnectionError on network failure."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            assert (
                "Connection refused" in str(exc_info.value)
                or "connection" in str(exc_info.value).lower()
            )

    async def test_send_raises_on_timeout(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises TimeoutError on request timeout."""
        from asap.transport.client import ASAPClient, ASAPTimeoutError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Request timed out")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPTimeoutError) as exc_info:
                await client.send(sample_request_envelope)

            assert "timeout" in str(exc_info.value).lower()

    async def test_send_raises_on_json_rpc_error(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises error on JSON-RPC error response."""
        from asap.transport.client import ASAPClient, ASAPRemoteError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return create_mock_error_response(-32601, "Method not found")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPRemoteError) as exc_info:
                await client.send(sample_request_envelope)

            assert exc_info.value.json_rpc_code == -32601
            assert "Method not found" in exc_info.value.message

    async def test_send_retries_on_recoverable_json_rpc_with_retry_after_ms(
        self,
        sample_request_envelope: Envelope,
        sample_response_envelope: Envelope,
    ) -> None:
        """Recoverable JSON-RPC error with retry_after_ms triggers a retry and eventual success."""
        from asap.transport.client import ASAPClient

        attempts: list[int] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(
                    status_code=200,
                    json={
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32002,
                            "message": "server asked to wait",
                            "data": {
                                "recoverable": True,
                                "retry_after_ms": 0,
                                "asap_taxonomy_code": "asap:protocol/invalid_timestamp",
                            },
                        },
                        "id": "req-1",
                    },
                )
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            response = await client.send(sample_request_envelope)

        assert len(attempts) == 2
        assert response.payload_type == "task.response"

    @pytest.mark.parametrize(
        "error_data",
        [
            {"recoverable": True},
            ["not", "an", "object"],
            "retry me",
        ],
    )
    async def test_send_does_not_retry_when_recoverable_hint_cannot_schedule(
        self,
        sample_request_envelope: Envelope,
        error_data: dict[str, bool] | list[str] | str,
    ) -> None:
        """One attempt when the hint cannot schedule a delay.

        Recoverable ``true`` without ``retry_after_ms`` must raise the remote
        error. A non-object ``data`` value must not be unpacked as a hint map.
        """
        from asap.transport.client import ASAPClient, ASAPRemoteError

        attempts: list[int] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(
                status_code=200,
                json={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32002,
                        "message": "server asked to wait",
                        "data": error_data,
                    },
                    "id": "req-1",
                },
            )

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            with pytest.raises(ASAPRemoteError) as exc_info:
                await client.send(sample_request_envelope)

        assert len(attempts) == 1
        if isinstance(error_data, dict):
            assert exc_info.value.is_recoverable is True
            assert exc_info.value.retry_after_ms is None
        else:
            assert exc_info.value.is_recoverable is False

    async def test_send_raises_on_http_error(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises error on HTTP error status."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, content=b"Internal Server Error")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            assert "500" in str(exc_info.value)

    async def test_send_raises_on_invalid_response(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises error on invalid JSON response."""
        from asap.transport.client import ASAPClient, ASAPRemoteError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"not json")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPRemoteError):
                await client.send(sample_request_envelope)


class TestASAPClientRetry(_CircuitBreakerRegistryCleanup):
    """Tests for ASAPClient retry logic."""

    async def test_send_includes_idempotency_key(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() includes idempotency_key in request for retries."""
        from asap.transport.client import ASAPClient

        captured_request: httpx.Request | None = None

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            await client.send(sample_request_envelope)

        assert captured_request is not None
        body = json.loads(captured_request.content)
        # idempotency_key should be in params or as header
        assert (
            "idempotency_key" in body.get("params", {})
            or "x-idempotency-key" in captured_request.headers
        )

    async def test_send_same_idempotency_key_on_retry(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() uses same idempotency_key when retrying."""
        from asap.transport.client import ASAPClient

        call_count = 0
        captured_keys: list[str] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            body = json.loads(request.content)
            key = body.get("params", {}).get("idempotency_key") or request.headers.get(
                "x-idempotency-key"
            )
            if key:
                captured_keys.append(key)

            if call_count < 2:
                raise httpx.ConnectError("Temporary failure")
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            await client.send(sample_request_envelope)

        # Should have retried and used same key
        assert call_count == 2
        assert len(captured_keys) >= 1

    async def test_send_retries_on_5xx_server_errors(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() retries on 5xx server errors."""
        from asap.transport.client import ASAPClient

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Return 5xx errors for first 2 attempts, succeed on 3rd
            if call_count < 3:
                return httpx.Response(status_code=503, content=b"Service Unavailable")
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            response = await client.send(sample_request_envelope)

        # Should have retried and eventually succeeded
        assert call_count == 3
        assert response.payload_type == "task.response"

    async def test_send_raises_after_max_retries_on_5xx(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test send() raises ASAPConnectionError after max retries on 5xx errors."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Always return 5xx error
            return httpx.Response(status_code=502, content=b"Bad Gateway")

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            # Should have attempted max_retries times
            assert call_count == 3
            assert "502" in str(exc_info.value)


class TestASAPClientRetryEdgeCases(_CircuitBreakerRegistryCleanup):
    """Tests for ASAPClient retry edge cases and error scenarios."""

    async def test_send_raises_when_not_connected(self, sample_request_envelope: Envelope) -> None:
        """Test send() raises ASAPConnectionError when called outside context manager."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        client = ASAPClient("http://localhost:8000")
        # Calling send() without entering context manager
        with pytest.raises(ASAPConnectionError) as exc_info:
            await client.send(sample_request_envelope)

        assert "not connected" in str(exc_info.value).lower()
        assert "async with" in str(exc_info.value)

    async def test_send_raises_on_none_envelope(self) -> None:
        """Test send() raises ValueError when envelope is None."""
        from asap.transport.client import ASAPClient

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"jsonrpc": "2.0", "result": {}})

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ValueError, match="envelope cannot be None"):
                await client.send(None)

    async def test_send_raises_on_max_retries_exceeded(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test send() raises ASAPConnectionError when all retry attempts fail."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Always fail with connection error
            raise httpx.ConnectError("Server unavailable")

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=3,
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            # Should have attempted max_retries times
            assert call_count == 3
            assert "connection" in str(exc_info.value).lower()

    async def test_send_recovers_from_intermittent_network_errors(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test send() recovers when network errors are intermittent."""
        from asap.transport.client import ASAPClient

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Fail first 2 attempts, succeed on 3rd
            if call_count < 3:
                raise httpx.ConnectError("Temporary network issue")
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=5,
        ) as client:
            response = await client.send(sample_request_envelope)

        # Should have retried and eventually succeeded
        assert call_count == 3
        assert response.payload_type == "task.response"

    async def test_send_raises_on_missing_envelope_in_response(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test send() raises ASAPRemoteError when response is missing envelope."""
        from asap.transport.client import ASAPClient, ASAPRemoteError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            # Return valid JSON-RPC response but without envelope
            json_rpc_response = {
                "jsonrpc": "2.0",
                "result": {"status": "ok"},  # Missing 'envelope' key
                "id": "req-1",
            }
            return httpx.Response(status_code=200, json=json_rpc_response)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPRemoteError) as exc_info:
                await client.send(sample_request_envelope)

            assert exc_info.value.json_rpc_code == -32603
            assert "missing envelope" in str(exc_info.value).lower()

    async def test_send_wraps_unexpected_exceptions(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test send() wraps unexpected exceptions in ASAPConnectionError."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            # Raise an unexpected exception type
            raise ValueError("Unexpected internal error")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            assert "unexpected" in str(exc_info.value).lower()
            assert exc_info.value.cause is not None
            assert isinstance(exc_info.value.cause, ValueError)

    async def test_idempotency_key_consistent_across_all_retries(
        self, sample_request_envelope: Envelope, sample_response_envelope: Envelope
    ) -> None:
        """Test idempotency_key remains consistent across all retry attempts."""
        from asap.transport.client import ASAPClient

        captured_keys: list[str] = []
        captured_header_keys: list[str] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            param_key = body.get("params", {}).get("idempotency_key")
            header_key = request.headers.get("x-idempotency-key")

            if param_key:
                captured_keys.append(param_key)
            if header_key:
                captured_header_keys.append(header_key)

            # Fail first 2 attempts
            if len(captured_keys) < 3:
                raise httpx.ConnectError("Retry needed")
            return create_mock_response(sample_response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=5,
        ) as client:
            await client.send(sample_request_envelope)

        # All captured keys should be identical
        assert len(captured_keys) == 3
        assert all(k == captured_keys[0] for k in captured_keys)
        # Header keys should also be identical
        assert len(captured_header_keys) == 3
        assert all(k == captured_header_keys[0] for k in captured_header_keys)

    async def test_aexit_handles_none_client(self) -> None:
        """Test __aexit__ handles case when client was never connected."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://localhost:8000")
        # Manually call __aexit__ without entering context
        await client.__aexit__(None, None, None)
        # Should not raise any exception
        assert not client.is_connected


class TestASAPClientStreaming:
    """Tests for ``ASAPClient.stream`` (SSE /asap/stream)."""

    @pytest.mark.anyio
    async def test_client_stream_parses_sse_chunks(
        self,
        sample_manifest: "Manifest",
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """``ASAPClient.stream`` reads ``/asap/stream`` SSE ``data:`` lines as envelopes."""
        from httpx import ASGITransport

        from asap.transport.client import ASAPClient
        from asap.transport.handlers import HandlerRegistry, create_echo_handler
        from asap.transport.server import create_app
        from tests.transport.test_streaming import _word_stream_handler

        registry = HandlerRegistry()
        registry.register("task.request", create_echo_handler())
        registry.register_streaming_handler("task.request", _word_stream_handler)
        app = create_app(sample_manifest, registry, rate_limit="999999/minute")
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter

        req_env = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="c-stream",
                skill_id="echo",
                input={"text": "x y"},
            ).model_dump(),
        )
        transport = ASGITransport(app=app)
        async with ASAPClient(
            "http://test-agent",
            transport=transport,
            require_https=False,
        ) as client:
            out = [e async for e in client.stream(req_env)]
        assert len(out) == 2
        assert out[-1].payload_dict.get("final") is True


class TestASAPClientHTTPSValidation:
    """Tests for HTTPS enforcement in ASAPClient initialization."""

    def test_https_urls_accepted(self) -> None:
        """Test that HTTPS URLs are accepted."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("https://example.com")
        assert client.base_url == "https://example.com"
        assert client.require_https is True

    def test_http_localhost_accepted_with_warning(self) -> None:
        """Test that HTTP localhost URLs are accepted with warning."""
        from asap.transport.client import ASAPClient

        # Should not raise, but will log warning
        client = ASAPClient("http://localhost:8000")
        assert client.base_url == "http://localhost:8000"
        assert client.require_https is True

    def test_http_127_0_0_1_accepted_with_warning(self) -> None:
        """Test that HTTP 127.0.0.1 URLs are accepted with warning."""
        from asap.transport.client import ASAPClient

        # Should not raise, but will log warning
        client = ASAPClient("http://127.0.0.1:8000")
        assert client.base_url == "http://127.0.0.1:8000"
        assert client.require_https is True

    def test_http_production_rejected(self) -> None:
        """Test that HTTP URLs for non-localhost are rejected."""
        from asap.transport.client import ASAPClient

        with pytest.raises(ValueError) as exc_info:
            ASAPClient("http://example.com")

        assert "Encrypted transport" in str(exc_info.value)
        assert "require_https=False" in str(exc_info.value)

    def test_require_https_rejects_ws_non_localhost(self) -> None:
        """Test that ws:// URLs for non-localhost are rejected when require_https=True."""
        from asap.transport.client import ASAPClient

        with pytest.raises(ValueError) as exc_info:
            ASAPClient(
                "ws://example.com",
                transport_mode="websocket",
            )

        assert "Encrypted transport" in str(exc_info.value)
        assert "wss" in str(exc_info.value).lower() or "WSS" in str(exc_info.value)
        assert "require_https=False" in str(exc_info.value)

    def test_ws_localhost_accepted_with_warning(self) -> None:
        """Test that ws:// localhost URLs are accepted with warning (same as HTTP localhost)."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("ws://127.0.0.1:8000", transport_mode="websocket")
        assert client.base_url == "ws://127.0.0.1:8000"
        assert client.require_https is True

    def test_http_with_override_works(self) -> None:
        """Test that HTTP URLs work when require_https=False."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("http://example.com", require_https=False)
        assert client.base_url == "http://example.com"
        assert client.require_https is False

    def test_https_with_require_https_false_works(self) -> None:
        """Test that HTTPS URLs work even when require_https=False."""
        from asap.transport.client import ASAPClient

        client = ASAPClient("https://example.com", require_https=False)
        assert client.base_url == "https://example.com"
        assert client.require_https is False


class TestASAPClientCustomErrors:
    """Tests for ASAP client custom error classes."""

    def test_asap_connection_error_is_exception(self) -> None:
        """Test ASAPConnectionError is an exception."""
        from asap.transport.client import ASAPConnectionError

        error = ASAPConnectionError("Test error")
        assert isinstance(error, Exception)
        assert "Test error" in str(error)

    def test_asap_timeout_error_is_exception(self) -> None:
        """Test ASAPTimeoutError is an exception."""
        from asap.transport.client import ASAPTimeoutError

        error = ASAPTimeoutError("Timeout occurred")
        assert isinstance(error, Exception)
        assert "Timeout" in str(error)

    def test_asap_remote_error_has_code_and_message(self) -> None:
        """Test ASAPRemoteError has code and message attributes."""
        from asap.transport.client import ASAPRemoteError

        error = ASAPRemoteError(-32601, "Method not found", {"method": "unknown"})
        assert error.json_rpc_code == -32601
        assert error.message == "Method not found"
        assert error.data == {"method": "unknown"}

    def test_asap_remote_error_str_includes_details(self) -> None:
        """Test ASAPRemoteError string includes code and message."""
        from asap.transport.client import ASAPRemoteError

        error = ASAPRemoteError(-32602, "Invalid params")
        error_str = str(error)
        assert "-32602" in error_str
        assert "Invalid params" in error_str


class TestImprovedConnectionErrorMessages:
    """Tests for improved connection error messages with troubleshooting guidance."""

    async def test_connection_error_includes_url_and_troubleshooting(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that ASAPConnectionError includes URL and troubleshooting suggestions."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            error_message = str(exc_info.value)
            # Should include URL
            assert "localhost:8000" in error_message or "http://localhost:8000" in error_message
            # Should include troubleshooting suggestion
            assert "Verify" in error_message or "verify" in error_message.lower()
            assert exc_info.value.url == "http://localhost:8000"

    async def test_connection_error_after_retries_includes_context(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that connection errors after retries include attempt context."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Server unavailable")

        async with ASAPClient(
            "http://example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
            require_https=False,
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            error_message = str(exc_info.value)
            # Should include URL
            assert "example.com" in error_message
            # Should include troubleshooting
            assert "Verify" in error_message or "verify" in error_message.lower()
            assert exc_info.value.url == "http://example.com"

    async def test_server_error_includes_url_and_context(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that server errors (5xx) include URL and context."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b"Service Unavailable")

        async with ASAPClient(
            "http://api.example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
            require_https=False,
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            error_message = str(exc_info.value)
            # Should include URL
            assert "api.example.com" in error_message
            # Should include status code
            assert "503" in error_message
            assert exc_info.value.url == "http://api.example.com"

    async def test_client_error_includes_troubleshooting(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that client errors (4xx) include troubleshooting information."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=400, content=b"Bad Request")

        async with ASAPClient(
            "http://api.example.com",
            transport=httpx.MockTransport(mock_transport),
            require_https=False,
        ) as client:
            with pytest.raises(ASAPConnectionError) as exc_info:
                await client.send(sample_request_envelope)

            error_message = str(exc_info.value)
            # Should include URL
            assert "api.example.com" in error_message
            # Should include status code
            assert "400" in error_message
            # Should indicate it's a client error
            assert (
                "client error" in error_message.lower()
                or "problem with the request" in error_message.lower()
            )
            assert exc_info.value.url == "http://api.example.com"

    async def test_timeout_error_includes_context(self, sample_request_envelope: Envelope) -> None:
        """Test that timeout errors include context and troubleshooting."""
        from asap.transport.client import ASAPClient, ASAPTimeoutError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Request timed out")

        async with ASAPClient(
            "http://slow-api.example.com",
            transport=httpx.MockTransport(mock_transport),
            timeout=5.0,
            require_https=False,
        ) as client:
            with pytest.raises(ASAPTimeoutError) as exc_info:
                await client.send(sample_request_envelope)

            error_message = str(exc_info.value)
            # Should include timeout value
            assert "5" in error_message or "timeout" in error_message.lower()
            assert exc_info.value.timeout == 5.0

    async def test_connection_validation_helper(self, sample_request_envelope: Envelope) -> None:
        """Test the _validate_connection helper method."""
        from asap.transport.client import ASAPClient

        # Test with successful connection
        def mock_transport_success(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(status_code=200)
            return httpx.Response(status_code=200, json={"jsonrpc": "2.0", "result": {}})

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport_success)
        ) as client:
            is_valid = await client._validate_connection()
            assert is_valid is True

        # Test with connection failure
        def mock_transport_failure(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport_failure)
        ) as client:
            is_valid = await client._validate_connection()
            assert is_valid is False


class TestASAPClientSendBatch:
    """Tests for ASAPClient.send_batch() method."""

    @pytest.fixture
    def multiple_request_envelopes(self) -> list[Envelope]:
        """Create multiple request envelopes for batch testing."""
        envelopes = []
        for i in range(5):
            envelopes.append(
                Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:client",
                    recipient="urn:asap:agent:server",
                    payload_type="task.request",
                    payload=TaskRequest(
                        conversation_id=f"conv_{i}",
                        skill_id="echo",
                        input={"message": f"Hello {i}!"},
                    ).model_dump(),
                )
            )
        return envelopes

    async def test_send_batch_returns_all_responses(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() returns response for each envelope."""
        from asap.transport.client import ASAPClient

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{envelope_data['id']}",
                    status=TaskStatus.COMPLETED,
                    result={"echoed": envelope_data["payload"]},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            responses = await client.send_batch(multiple_request_envelopes)

        assert len(responses) == len(multiple_request_envelopes)
        for i, response in enumerate(responses):
            assert isinstance(response, Envelope)
            assert response.payload_type == "task.response"
            assert response.correlation_id == multiple_request_envelopes[i].id

    async def test_send_batch_preserves_order(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() preserves order of responses matching inputs."""
        from asap.transport.client import ASAPClient

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]
            # Include request envelope id in response to verify order
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{envelope_data['id']}",
                    status=TaskStatus.COMPLETED,
                    result={"original_id": envelope_data["id"]},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            responses = await client.send_batch(multiple_request_envelopes)

        # Verify order is preserved by checking correlation_id matches
        for i, response in enumerate(responses):
            assert isinstance(response, Envelope)
            assert response.correlation_id == multiple_request_envelopes[i].id

    async def test_send_batch_empty_list_raises_error(self) -> None:
        """Test send_batch() raises ValueError for empty list."""
        from asap.transport.client import ASAPClient

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={})

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ValueError, match="envelopes list cannot be empty"):
                await client.send_batch([])

    async def test_send_batch_not_connected_raises_error(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() raises error when not connected."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        client = ASAPClient("http://localhost:8000")
        with pytest.raises(ASAPConnectionError, match="not connected"):
            await client.send_batch(multiple_request_envelopes)

    async def test_send_batch_with_return_exceptions_true(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() returns exceptions when return_exceptions=True."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]

            # Fail for envelope index 2 (third envelope based on conversation_id)
            if "conv_2" in envelope_data.get("payload", {}).get("conversation_id", ""):
                raise httpx.ConnectError("Server unavailable for conv_2")

            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{envelope_data['id']}",
                    status=TaskStatus.COMPLETED,
                    result={"echoed": True},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,  # Reduce retries for faster test
        ) as client:
            results = await client.send_batch(multiple_request_envelopes, return_exceptions=True)

        assert len(results) == 5
        successful = [r for r in results if isinstance(r, Envelope)]
        failed = [r for r in results if isinstance(r, BaseException)]

        assert len(successful) == 4
        assert len(failed) == 1
        assert isinstance(failed[0], ASAPConnectionError)

    async def test_send_batch_without_return_exceptions_raises(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() raises exception when return_exceptions=False."""
        from asap.transport.client import ASAPClient, ASAPConnectionError

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]

            # Fail for second envelope
            if "conv_1" in envelope_data.get("payload", {}).get("conversation_id", ""):
                raise httpx.ConnectError("Server unavailable")

            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_1",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=1,
        ) as client:
            with pytest.raises(ASAPConnectionError):
                await client.send_batch(multiple_request_envelopes, return_exceptions=False)

    async def test_send_batch_concurrent_execution(
        self, multiple_request_envelopes: list[Envelope]
    ) -> None:
        """Test send_batch() executes requests concurrently."""
        import asyncio

        from asap.transport.client import ASAPClient

        start_times: list[float] = []
        end_times: list[float] = []
        lock = asyncio.Lock()

        async def delayed_response(request: httpx.Request) -> httpx.Response:
            # Track timing
            async with lock:
                start_times.append(asyncio.get_event_loop().time())

            # Small delay to simulate network
            await asyncio.sleep(0.01)

            async with lock:
                end_times.append(asyncio.get_event_loop().time())

            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{envelope_data['id']}",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        # Using httpx.MockTransport won't work with async delays, so we use a custom transport
        class AsyncMockTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return await delayed_response(request)

        async with ASAPClient(
            "http://localhost:8000",
            transport=AsyncMockTransport(),
        ) as client:
            await client.send_batch(multiple_request_envelopes)

        # With concurrent execution, all requests should start roughly at the same time
        # If sequential, the time span would be ~5x larger
        assert len(start_times) == 5
        time_span = max(start_times) - min(start_times)
        # With concurrent execution, time span should be very small (< 50ms)
        # Sequential would be > 50ms (5 * 10ms delays)
        assert time_span < 0.05, f"Requests not concurrent: time span = {time_span}s"

    async def test_send_batch_single_envelope(self) -> None:
        """Test send_batch() works with single envelope."""
        from asap.transport.client import ASAPClient

        single_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_single",
                skill_id="echo",
                input={"message": "Single"},
            ).model_dump(),
        )

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_single",
                    status=TaskStatus.COMPLETED,
                    result={"echoed": "Single"},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            responses = await client.send_batch([single_envelope])

        assert len(responses) == 1
        assert isinstance(responses[0], Envelope)
        assert responses[0].correlation_id == single_envelope.id

    async def test_send_batch_large_batch(self) -> None:
        """Test send_batch() handles large batches efficiently."""
        from asap.transport.client import ASAPClient

        # Create 50 envelopes
        large_batch = [
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:client",
                recipient="urn:asap:agent:server",
                payload_type="task.request",
                payload=TaskRequest(
                    conversation_id=f"conv_{i}",
                    skill_id="echo",
                    input={"index": i},
                ).model_dump(),
            )
            for i in range(50)
        ]

        def mock_transport(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            envelope_data = body["params"]["envelope"]
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id=f"task_{envelope_data['id']}",
                    status=TaskStatus.COMPLETED,
                    result={"processed": True},
                ).model_dump(),
                correlation_id=envelope_data["id"],
            )
            return create_mock_response(response_envelope)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            responses = await client.send_batch(large_batch)

        assert len(responses) == 50
        for response in responses:
            assert isinstance(response, Envelope)
            assert response.payload_type == "task.response"


class TestRetryBehavior(_CircuitBreakerRegistryCleanup):
    def test_init_with_retry_config(self) -> None:
        config = RetryConfig(
            max_retries=10,
            base_delay=2.0,
            max_delay=100.0,
            jitter=False,
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=3,
            circuit_breaker_timeout=30.0,
        )
        client = ASAPClient("https://example.com", retry_config=config)

        assert client.max_retries == 10
        assert client.base_delay == 2.0
        assert client.max_delay == 100.0
        assert client.jitter is False
        assert client.circuit_breaker_enabled is True
        assert client._circuit_breaker is not None
        assert client._circuit_breaker.threshold == 3
        assert client._circuit_breaker.timeout == 30.0

    @pytest.mark.asyncio
    async def test_retry_after_future_date(self, sample_request_envelope: Envelope) -> None:
        frozen_now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        frozen_timestamp = frozen_now.timestamp()
        future_date = frozen_now + timedelta(seconds=10)
        future_date_str = future_date.strftime("%a, %d %b %Y %H:%M:%S GMT")

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    status_code=429,
                    content=b"Rate Limited",
                    headers={"Retry-After": future_date_str},
                )
            return create_mock_response(
                Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:server",
                    recipient="urn:asap:agent:client",
                    payload_type="task.response",
                    payload=TaskResponse(
                        task_id="t", status=TaskStatus.COMPLETED, result={}
                    ).model_dump(),
                    correlation_id=sample_request_envelope.id,
                )
            )

        with (
            patch("asap.transport.client.asyncio.sleep") as mock_sleep,
            patch("asap.transport.client.time.time", return_value=frozen_timestamp),
        ):

            async def mock_sleep_fn(delay: float) -> None:
                pass

            mock_sleep.side_effect = mock_sleep_fn

            async with ASAPClient(
                "https://example.com",
                transport=httpx.MockTransport(mock_transport),
                max_retries=2,
                jitter=False,
            ) as client:
                await client.send(sample_request_envelope)

            sleep_args = [c[0][0] for c in mock_sleep.call_args_list]
            found = any(9.0 <= a <= 11.0 for a in sleep_args)
            assert found, f"No sleep(~10s) found in calls: {sleep_args}"

    @pytest.mark.asyncio
    async def test_validate_connection_raises_when_not_connected(self) -> None:
        client = ASAPClient("https://example.com")
        with pytest.raises(ASAPConnectionError, match="not connected"):
            await client._validate_connection()

    @pytest.mark.asyncio
    async def test_validate_connection_raises_when_client_is_none(self) -> None:
        client = ASAPClient("https://example.com")
        client._client = None
        with pytest.raises(ASAPConnectionError, match="not connected"):
            await client._validate_connection()

    @pytest.mark.asyncio
    async def test_validate_connection_success(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            if "/asap/manifest" in str(request.url):
                return httpx.Response(status_code=200, json={"name": "test-agent"})
            return httpx.Response(status_code=404)

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            result = await client._validate_connection()
            assert result is True

    @pytest.mark.asyncio
    async def test_validate_connection_failure(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503)

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            result = await client._validate_connection()
            assert result is False

    @pytest.mark.asyncio
    async def test_validate_connection_connect_error_returns_false(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            result = await client._validate_connection()
            assert result is False

    @pytest.mark.asyncio
    async def test_validate_connection_timeout_returns_false(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Timeout")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            result = await client._validate_connection()
            assert result is False

    @pytest.mark.asyncio
    async def test_validate_connection_generic_exception_returns_false(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("Unexpected error")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            result = await client._validate_connection()
            assert result is False

    @pytest.mark.asyncio
    async def test_send_5xx_retry_then_circuit_opens(self) -> None:
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=503, content=b"Service Unavailable")

        large_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_123",
                skill_id="echo",
                input={"message": "x" * 200},
            ).model_dump(),
        )

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            compression=True,
            compression_threshold=50,
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=2,
            max_retries=2,
        ) as client:
            with pytest.raises(ASAPConnectionError, match="503"):
                await client.send(large_envelope)
            assert call_count == 2

            with pytest.raises(ASAPConnectionError, match="503"):
                await client.send(large_envelope)
            assert call_count == 4
            assert client._circuit_breaker is not None
            assert client._circuit_breaker.get_state() == CircuitState.OPEN

            with pytest.raises(CircuitOpenError):
                await client.send(large_envelope)
            assert call_count == 4

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_exhausted_5xx(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that circuit breaker records failure when retries are exhausted for 5xx."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b"Unavailable")

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=5,
        ) as client:
            with pytest.raises(ASAPConnectionError):
                await client.send(sample_request_envelope)

            assert client._circuit_breaker.get_consecutive_failures() == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_exhausted_connect_error(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that circuit breaker records failure when retries are exhausted for connection error."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Failed")

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
            circuit_breaker_enabled=True,
        ) as client:
            with pytest.raises(ASAPConnectionError):
                await client.send(sample_request_envelope)

            assert client._circuit_breaker.get_consecutive_failures() == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_exhausted_429(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that circuit breaker records failure when retries are exhausted for 429."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=429, content=b"Rate Limited")

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
            circuit_breaker_enabled=True,
            base_delay=0.01,
            jitter=False,
        ) as client:
            with pytest.raises(ASAPConnectionError, match="rate limit"):
                await client.send(sample_request_envelope)

            assert client._circuit_breaker.get_consecutive_failures() == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_exhausted_timeout(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that circuit breaker records failure when retries are exhausted for timeouts."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Timed out")

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            max_retries=2,
            circuit_breaker_enabled=True,
            base_delay=0.01,
            jitter=False,
        ) as client:
            with pytest.raises(ASAPTimeoutError):
                await client.send(sample_request_envelope)

            assert client._circuit_breaker.get_consecutive_failures() == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_400_error(
        self, sample_request_envelope: Envelope
    ) -> None:
        """Test that circuit breaker records failure on non-retriable 4xx errors."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=400, content=b"Bad Request")

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            circuit_breaker_enabled=True,
        ) as client:
            with pytest.raises(ASAPConnectionError, match="400"):
                await client.send(sample_request_envelope)

            assert client._circuit_breaker.get_consecutive_failures() == 1

    def test_url_validation_missing_netloc(self) -> None:
        with pytest.raises(ValueError, match="Invalid base_url format"):
            ASAPClient("http:///path")

    def test_is_localhost_helper(self) -> None:
        client = ASAPClient("https://example.com")
        from urllib.parse import urlparse

        assert client._is_localhost(urlparse("http://localhost:8000")) is True
        assert client._is_localhost(urlparse("http://127.0.0.1:8000")) is True
        assert client._is_localhost(urlparse("http://[::1]")) is True
        assert client._is_localhost(urlparse("http://example.com")) is False
        assert client._is_localhost(urlparse("http://192.168.1.1")) is False

    def test_is_localhost_no_hostname_returns_false(self) -> None:
        client = ASAPClient("https://example.com")
        from urllib.parse import urlparse

        assert client._is_localhost(urlparse("file:///tmp/foo")) is False

    @pytest.mark.asyncio
    async def test_retry_after_invalid_date_format(self, sample_request_envelope: Envelope) -> None:
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    status_code=429,
                    content=b"Rate Limited",
                    headers={"Retry-After": "Invalid Date Format"},
                )
            return create_mock_response(sample_request_envelope)

        with patch("asap.transport.client.asyncio.sleep") as mock_sleep:

            async def mock_sleep_fn(delay: float) -> None:
                pass

            mock_sleep.side_effect = mock_sleep_fn

            async with ASAPClient(
                "https://example.com",
                transport=httpx.MockTransport(mock_transport),
                max_retries=2,
                base_delay=0.1,
                jitter=False,
            ) as client:
                await client.send(sample_request_envelope)

            assert call_count == 2
            call_args_list = [c[0] for c in mock_sleep.call_args_list]
            assert (0.1,) in call_args_list, (
                f"Expected sleep(0.1) among calls, got {call_args_list[:5]}..."
            )


class TestManifestCache:
    @pytest.mark.asyncio
    async def test_get_manifest_cache_hit(self) -> None:
        from asap.models.entities import Manifest

        manifest_data = {
            "id": "urn:asap:agent:testagent",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test", "description": "Test skill"}],
                "state_persistence": False,
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }
        expected_manifest = Manifest(**manifest_data)

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, json=manifest_data)

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            client._manifest_cache.set(
                "https://example.com/.well-known/asap/manifest.json", expected_manifest
            )
            result = await client.get_manifest()

        assert call_count == 0
        assert result.id == expected_manifest.id

    @pytest.mark.asyncio
    async def test_get_manifest_cache_miss_then_set(self) -> None:
        manifest_data = {
            "id": "urn:asap:agent:testagent",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test", "description": "Test skill"}],
                "state_persistence": False,
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }

        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, json=manifest_data)

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            assert client._manifest_cache.size() == 0
            result = await client.get_manifest()
            assert call_count == 1
            assert result.id == "urn:asap:agent:testagent"
            assert client._manifest_cache.size() == 1
            cached = client._manifest_cache.get(
                "https://example.com/.well-known/asap/manifest.json"
            )
            assert cached is not None
            assert cached.id == "urn:asap:agent:testagent"

    @pytest.mark.asyncio
    async def test_get_manifest_cache_invalidate_on_http_error(self) -> None:
        from asap.models.entities import Manifest

        manifest_data = {
            "id": "urn:asap:agent:testagent",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test", "description": "Test skill"}],
                "state_persistence": False,
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }
        stale_manifest = Manifest(**manifest_data)

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, content=b"Internal Server Error")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            url = "https://example.com/.well-known/asap/manifest.json"
            client._manifest_cache.set(url, stale_manifest)
            assert client._manifest_cache.size() == 1
            client._manifest_cache.invalidate(url)
            assert client._manifest_cache.size() == 0

            with pytest.raises(ASAPConnectionError, match="500"):
                await client.get_manifest()

            assert client._manifest_cache.size() == 0

    @pytest.mark.asyncio
    async def test_get_manifest_cache_invalidate_on_invalid_json(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"not valid json")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ValueError, match="Invalid JSON"):
                await client.get_manifest()

            assert client._manifest_cache.size() == 0

    @pytest.mark.asyncio
    async def test_get_manifest_cache_invalidate_on_invalid_manifest(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"foo": "bar"})

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ValueError, match="Invalid manifest"):
                await client.get_manifest()

            assert client._manifest_cache.size() == 0

    @pytest.mark.asyncio
    async def test_get_manifest_cache_invalidate_on_timeout(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Timeout fetching manifest")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPTimeoutError):
                await client.get_manifest()

            assert client._manifest_cache.size() == 0

    @pytest.mark.asyncio
    async def test_get_manifest_cache_invalidate_on_connect_error(self) -> None:
        def mock_transport(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ASAPConnectionError):
                await client.get_manifest()

            assert client._manifest_cache.size() == 0

    @pytest.mark.asyncio
    async def test_get_manifest_concurrent_same_url_single_http_request(self) -> None:
        import asyncio

        manifest_data = {
            "id": "urn:asap:agent:testagent",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test", "description": "Test skill"}],
                "state_persistence": False,
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }

        manifest_get_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal manifest_get_count
            manifest_get_count += 1
            return httpx.Response(status_code=200, json=manifest_data)

        async with ASAPClient(
            "https://example.com", transport=httpx.MockTransport(mock_transport)
        ) as client:
            results = await asyncio.gather(
                *[client.get_manifest() for _ in range(10)],
                return_exceptions=True,
            )

        assert manifest_get_count == 1
        for r in results:
            assert not isinstance(r, BaseException), r
            assert r.id == "urn:asap:agent:testagent"


class TestManifestSignatureVerification:
    """Tests for get_manifest with verify_signatures and trusted_manifest_keys."""

    @staticmethod
    def _signed_manifest_payload() -> tuple[dict, str]:
        manifest = Manifest(
            id="urn:asap:agent:signed-test",
            name="Signed Agent",
            version="1.0.0",
            description="Agent with signed manifest",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="echo", description="Echo")],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        private_key, public_key = generate_keypair()
        signed = sign_manifest(manifest, private_key)
        return signed.model_dump(mode="json"), public_key_to_base64(public_key)

    @pytest.mark.asyncio
    async def test_get_manifest_signed_with_trusted_key_succeeds(self) -> None:
        payload, pub_b64 = self._signed_manifest_payload()
        manifest_url = "https://example.com/.well-known/asap/manifest.json"

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=payload)

        trusted = {manifest_url: pub_b64}
        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            verify_signatures=True,
            trusted_manifest_keys=trusted,
        ) as client:
            result = await client.get_manifest()
        assert result.id == "urn:asap:agent:signed-test"
        assert result.name == "Signed Agent"

    @pytest.mark.asyncio
    async def test_get_manifest_signed_without_trusted_key_raises(self) -> None:
        payload, _ = self._signed_manifest_payload()

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=payload)

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            verify_signatures=True,
            trusted_manifest_keys={},
        ) as client:
            with pytest.raises(SignatureVerificationError, match="no trusted public key"):
                await client.get_manifest()

    @pytest.mark.asyncio
    async def test_get_manifest_signed_with_wrong_trusted_key_raises(self) -> None:
        payload, _ = self._signed_manifest_payload()
        _, other_public_key = generate_keypair()
        wrong_pub_b64 = public_key_to_base64(other_public_key)
        manifest_url = "https://example.com/.well-known/asap/manifest.json"

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=payload)

        trusted = {manifest_url: wrong_pub_b64}
        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            verify_signatures=True,
            trusted_manifest_keys=trusted,
        ) as client:
            with pytest.raises(SignatureVerificationError, match="tamper|invalid"):
                await client.get_manifest()

    @pytest.mark.asyncio
    async def test_get_manifest_plain_json_when_verify_signatures_ignored(self) -> None:
        plain_data = {
            "id": "urn:asap:agent:plain",
            "name": "Plain Agent",
            "version": "1.0.0",
            "description": "No signature",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "echo", "description": "Echo"}],
                "state_persistence": False,
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=plain_data)

        async with ASAPClient(
            "https://example.com",
            transport=httpx.MockTransport(mock_transport),
            verify_signatures=True,
            trusted_manifest_keys={},
        ) as client:
            result = await client.get_manifest()
        assert result.id == "urn:asap:agent:plain"


class TestClientCorrelationBinding:
    """B6 (BUG #6): client must reject responses whose correlation_id does not bind to the request.

    The structural ``validate_response_correlation`` check only ensures the response carries a
    non-empty ``correlation_id``. The BINDING check (response.correlation_id == request.id)
    must be enforced at the client pairing site so a buggy/malicious server cannot return a
    response meant for a different request under concurrency.
    """

    async def test_send_rejects_mismatched_correlation_id(
        self, sample_request_envelope: Envelope
    ) -> None:
        """``send`` raises ``ProtocolCorrelationError`` when response correlation_id != request id."""
        # Non-empty but different from sample_request_envelope.id so it passes the
        # structural non-empty check but must fail the binding check.
        mismatched_response = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="task.response",
            payload=TaskResponse(
                task_id="task_456",
                status=TaskStatus.COMPLETED,
                result={"echoed": {"message": "Hello!"}},
            ).model_dump(),
            correlation_id="different-id",
        )

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return create_mock_response(mismatched_response)

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                await client.send(sample_request_envelope)

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr(sample_request_envelope.id) in message
            assert repr("different-id") in message

    async def test_stream_rejects_chunk_bound_to_request_correlation_id_not_id(
        self, sample_request_envelope: Envelope
    ) -> None:
        """#247: stream binding uses request.id even when request carries a distinct correlation_id."""
        request = sample_request_envelope.model_copy(update={"correlation_id": "corr-chain-A"})
        assert request.correlation_id is not None
        assert request.correlation_id != request.id

        bound_to_correlation = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="TaskStream",
            payload={"chunk": "partial", "final": True, "progress": 1.0},
            correlation_id=request.correlation_id,
        )

        def mock_transport(_: httpx.Request) -> httpx.Response:
            stream_body = (
                f"data: {json.dumps(bound_to_correlation.model_dump(mode='json'))}\n\n".encode(
                    "utf-8"
                )
            )
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                content=stream_body,
            )

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                _ = [chunk async for chunk in client.stream(request)]

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr(request.id) in message
            assert repr(request.correlation_id) in message

    async def test_stream_rejects_mismatched_taskstream_correlation_id(
        self, sample_request_envelope: Envelope
    ) -> None:
        """CR#4: ``stream`` rejects a ``TaskStream`` chunk bound to a different request id."""
        mismatched_chunk = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="TaskStream",
            payload={"chunk": "partial", "final": True, "progress": 1.0},
            correlation_id="different-id",
        )

        def mock_transport(_: httpx.Request) -> httpx.Response:
            stream_body = (
                f"data: {json.dumps(mismatched_chunk.model_dump(mode='json'))}\n\n".encode("utf-8")
            )
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                content=stream_body,
            )

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                _ = [chunk async for chunk in client.stream(sample_request_envelope)]

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr(sample_request_envelope.id) in message
            assert repr("different-id") in message

    async def test_stream_rejects_mismatched_taskresponse_correlation_id(
        self, sample_request_envelope: Envelope
    ) -> None:
        """CR#4: ``stream`` also rejects a streamed ``TaskResponse`` bound elsewhere."""
        mismatched_response = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="task.response",
            payload=TaskResponse(
                task_id="task_stream_response",
                status=TaskStatus.COMPLETED,
                result={"echoed": {"message": "Hello!"}},
            ).model_dump(),
            correlation_id="different-id",
        )

        def mock_transport(_: httpx.Request) -> httpx.Response:
            stream_body = (
                f"data: {json.dumps(mismatched_response.model_dump(mode='json'))}\n\n".encode(
                    "utf-8"
                )
            )
            return httpx.Response(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                content=stream_body,
            )

        async with ASAPClient(
            "http://localhost:8000", transport=httpx.MockTransport(mock_transport)
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                _ = [chunk async for chunk in client.stream(sample_request_envelope)]

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr(sample_request_envelope.id) in message
            assert repr("different-id") in message
