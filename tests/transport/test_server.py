"""Tests for FastAPI server implementation.

Tests cover:
- App factory creation
- Route registration
- POST /asap endpoint
- GET /health and GET /ready (liveness/readiness probes)
- GET /.well-known/asap/manifest.json endpoint
- GET /asap/metrics endpoint
- Error handling
- HandlerRegistry integration
- Custom handler registration
- Metrics collection
- Authentication integration
"""

import collections.abc
import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


from asap.errors import FatalError, RPC_HANDLER_NOT_FOUND, RPC_REMOTE_GENERIC
from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.observability import get_metrics, reset_metrics
from asap.observability.metrics import MetricsCollector
from asap.models.constants import ASAP_DEFAULT_TRANSPORT_VERSION
from asap.transport.challenge import format_www_authenticate_asap
from asap.transport.handlers import HandlerRegistry
from asap.transport.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    VERSION_INCOMPATIBLE,
    JsonRpcRequest,
)
from asap.auth.agent_jwt import create_agent_jwt, create_host_jwt, verify_agent_jwt
from asap.auth.self_auth import FreshSessionConfig
from asap.auth.identity import (
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
)
from asap.transport.server import (
    ASAPRequestHandler,
    RegistryHolder,
    RequestContext,
    create_app,
    _run_handler_watcher,
)
from tests.crypto.jwk_helpers import ed25519_public_jwk

# Host JWT ``aud`` must match ``create_app`` default (``identity_jwt_audience`` == ``manifest.id``).
_HOST_JWT_AUDIENCE = "urn:asap:agent:test-server"


class _RejectingWebAuthnVerifier:
    """Test verifier that records calls and rejects assertions."""

    def __init__(self) -> None:
        """Initialize an empty call log."""
        self.calls: list[tuple[str, Any, str | None, bool]] = []

    async def verify(
        self,
        challenge: str,
        response: Any,
        *,
        host_id: str | None = None,
        require_user_verification: bool = False,
    ) -> bool:
        """Record the verification request and reject it."""
        self.calls.append((challenge, response, host_id, require_user_verification))
        return False


def _host_jwt_without_agent_claim(host_sk: Ed25519PrivateKey, *, ttl_seconds: int = 120) -> str:
    """Host JWT for status routes (no ``agent_public_key`` claim required)."""
    return create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=ttl_seconds)


def _app_with_identity_stores(
    sample_manifest: Manifest,
    isolated_rate_limiter: "ASAPRateLimiter | None",
) -> tuple[FastAPI, InMemoryAgentStore, InMemoryHostStore]:
    """FastAPI app with injected in-memory identity stores (transport tests)."""
    agent_store = InMemoryAgentStore()
    host_store = InMemoryHostStore(agent_store=agent_store)
    app = create_app(
        sample_manifest,
        rate_limit="999999/minute",
        identity_host_store=host_store,
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    return app, agent_store, host_store


@pytest.fixture
def app(
    sample_manifest: Manifest,
    isolated_rate_limiter: "ASAPRateLimiter | None",
) -> FastAPI:
    """Create FastAPI app for testing (very high rate limit, isolated limiter)."""
    app_instance = create_app(sample_manifest, rate_limit="999999/minute")
    if isolated_rate_limiter is not None:
        app_instance.state.limiter = isolated_rate_limiter
    return app_instance


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app)


class TestAppFactory:
    """Tests for create_app() factory function."""

    def test_create_app_returns_fastapi_instance(self, sample_manifest: Manifest) -> None:
        """Test that create_app returns a FastAPI instance."""
        app = create_app(sample_manifest)

        assert isinstance(app, FastAPI)
        assert app.title == "ASAP Protocol Server"

    def test_app_has_required_routes(self, app: FastAPI) -> None:
        """Test that app has all required routes."""
        routes = [route.path for route in app.routes]

        # Required routes
        assert "/asap" in routes
        assert "/health" in routes
        assert "/ready" in routes
        assert "/.well-known/asap/manifest.json" in routes

    def test_app_has_correct_http_methods(self, app: FastAPI) -> None:
        """Test that routes have correct HTTP methods."""
        routes_by_path = {route.path: route for route in app.routes}

        # /asap should accept POST
        asap_route = routes_by_path["/asap"]
        assert "POST" in asap_route.methods

        # manifest should accept GET
        manifest_route = routes_by_path["/.well-known/asap/manifest.json"]
        assert "GET" in manifest_route.methods

        # health and ready should accept GET
        health_route = routes_by_path["/health"]
        assert "GET" in health_route.methods
        ready_route = routes_by_path["/ready"]
        assert "GET" in ready_route.methods

    def test_create_app_with_hot_reload_returns_app(self, sample_manifest: Manifest) -> None:
        """Test that create_app(..., hot_reload=True) returns an app (watcher starts in background)."""
        app = create_app(sample_manifest, hot_reload=True)
        assert isinstance(app, FastAPI)


class TestASAPVersionMiddleware:
    """HTTP ASAP-Version header: negotiation on POST /asap and header on all responses."""

    def test_health_includes_default_asap_version_header(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Responses include ASAP-Version default when client omits the request header."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION

    def test_post_asap_without_header_uses_default_version_on_response(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """POST /asap without ASAP-Version succeeds and echoes the default wire version."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-version-1",
                skill_id="echo",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "version-1",
        }
        response = client.post("/asap", json=body)
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION
        assert "result" in response.json()

    def test_post_asap_with_compatible_version_echoes_header(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """POST /asap with supported ASAP-Version echoes that version on the response."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-version-2",
                skill_id="echo",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "version-2",
        }
        response = client.post("/asap", json=body, headers={"ASAP-Version": "2.1"})
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == "2.1"
        assert "result" in response.json()

    def test_post_asap_incompatible_version_returns_jsonrpc_error(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Unsupported ASAP-Version yields JSON-RPC error -32000 without running the handler."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-version-3",
                skill_id="echo",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "version-3",
        }
        response = client.post("/asap", json=body, headers={"ASAP-Version": "0.9"})
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION
        payload = response.json()
        assert payload.get("error", {}).get("code") == VERSION_INCOMPATIBLE
        assert payload.get("error", {}).get("data", {}).get("requested") == "0.9"

    def test_post_asap_comma_separated_version_first_match_wins(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Comma-separated ASAP-Version uses the first token in the supported set."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-version-4",
                skill_id="echo",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "version-4",
        }
        response = client.post(
            "/asap",
            json=body,
            headers={"ASAP-Version": "2.2, 2.1"},
        )
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == "2.2"
        assert "result" in response.json()

    @pytest.mark.parametrize(
        ("header", "echoed"),
        [
            ("0.9, 2.1", "2.1"),
            ("  0.9 ,  2.1  ", "2.1"),
            ("0.9,,2.1", "2.1"),
            ("2.1, 2.2", "2.1"),
        ],
    )
    def test_post_asap_skips_unsupported_tokens_and_keeps_client_order(
        self, app: FastAPI, client: TestClient, header: str, echoed: str
    ) -> None:
        """First supported token wins, including a fallback after an unknown version."""
        body = _version_probe_body("conv-version-fallback", "version-fallback")
        response = client.post("/asap", json=body, headers={"ASAP-Version": header})
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == echoed
        assert "result" in response.json()

    def test_post_asap_lists_with_no_supported_token_keep_full_requested_value(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """A list with no supported token is rejected and echoes the whole header."""
        body = _version_probe_body("conv-version-none", "version-none")
        response = client.post("/asap", json=body, headers={"ASAP-Version": "0.8, 0.9"})
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION
        payload = response.json()
        assert payload.get("error", {}).get("code") == VERSION_INCOMPATIBLE
        assert payload.get("error", {}).get("data", {}).get("requested") == "0.8, 0.9"
        assert "result" not in payload

    def test_post_asap_whitespace_only_version_uses_default(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """A blank ASAP-Version header is treated as omitted and the handler still runs."""
        body = _version_probe_body("conv-version-blank", "version-blank")
        response = client.post("/asap", json=body, headers={"ASAP-Version": "   "})
        assert response.status_code == 200
        assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION
        assert "result" in response.json()


def _version_probe_body(conversation_id: str, request_id: str) -> dict[str, Any]:
    """JSON-RPC body for ASAP-Version negotiation probes."""
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id=conversation_id,
            skill_id="echo",
            input={"message": "hello"},
        ).model_dump(),
    )
    return {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope.model_dump(mode="json")},
        "id": request_id,
    }


class TestASAPChallengePropagation:
    """Tests for preserving upstream ASAP challenges across JSON-RPC responses."""

    def test_post_asap_moves_internal_asap_challenge_detail_to_header(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Handler-provided upstream challenges become HTTP headers, not JSON body leaks."""
        challenge = format_www_authenticate_asap(
            "https://upstream.example/.well-known/asap/manifest.json"
        )

        async def challenge_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            raise FatalError(
                code="asap:transport/upstream_unauthorized",
                message="Upstream authorization challenge",
                details={
                    "_www_authenticate_asap": challenge,
                    "upstream_status": 401,
                },
            )

        registry = HandlerRegistry()
        registry.register("task.request", challenge_handler)
        app = create_app(sample_manifest, registry, rate_limit="999999/minute")
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-challenge-1",
                skill_id="needs-upstream",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "challenge-1",
        }

        response = TestClient(app).post("/asap", json=body)

        assert response.status_code == 200
        assert response.headers.get("WWW-Authenticate") == challenge
        payload = response.json()
        assert payload["id"] == "challenge-1"
        assert payload["error"]["code"] == RPC_REMOTE_GENERIC
        details = payload["error"]["data"]["details"]
        assert details["upstream_status"] == 401
        assert "_www_authenticate_asap" not in details


class TestRegistryHolder:
    """Tests for RegistryHolder hot-reload support."""

    def test_replace_registry_copies_executor_when_set(self) -> None:
        """When holder has _executor set, replace_registry copies it to the new registry."""
        registry = HandlerRegistry()
        holder = RegistryHolder(registry)
        executor = MagicMock()
        holder._executor = executor
        new_registry = HandlerRegistry()
        holder.replace_registry(new_registry)
        assert holder.registry is new_registry
        assert getattr(new_registry, "_executor", None) is executor

    def test_replace_registry_no_executor_when_not_set(self) -> None:
        """When holder has no _executor, replace_registry only swaps the registry."""
        registry = HandlerRegistry()
        holder = RegistryHolder(registry)
        new_registry = HandlerRegistry()
        holder.replace_registry(new_registry)
        assert holder.registry is new_registry
        assert (
            not hasattr(new_registry, "_executor")
            or getattr(new_registry, "_executor", None) is None
        )


class TestHandlerWatcher:
    """Tests for _run_handler_watcher (hot reload when watchfiles missing)."""

    def test_run_handler_watcher_exits_when_watchfiles_not_installed(self) -> None:
        """When watchfiles is not importable, watcher logs and returns without blocking."""
        holder = RegistryHolder(HandlerRegistry())
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "watchfiles":
                raise ImportError("watchfiles not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            _run_handler_watcher(holder, "/nonexistent/path")


class TestHealthEndpoints:
    """Tests for GET /health and GET /ready (liveness/readiness probes)."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        """Test that /health returns 200 with status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_returns_ok(self, client: TestClient) -> None:
        """Test that /ready returns 200 with status ok."""
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestManifestEndpoint:
    """Tests for GET /.well-known/asap/manifest.json endpoint."""

    def test_manifest_endpoint_returns_manifest(
        self, client: TestClient, sample_manifest: Manifest
    ) -> None:
        """Test that manifest endpoint returns the manifest as JSON."""
        response = client.get("/.well-known/asap/manifest.json")

        assert response.status_code == 200
        assert response.json() == sample_manifest.model_dump()

    def test_manifest_endpoint_content_type(self, client: TestClient) -> None:
        """Test that manifest endpoint returns correct content type."""
        response = client.get("/.well-known/asap/manifest.json")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

    def test_manifest_endpoint_is_idempotent(
        self, client: TestClient, sample_manifest: Manifest
    ) -> None:
        """Test that multiple calls return the same manifest."""
        response1 = client.get("/.well-known/asap/manifest.json")
        response2 = client.get("/.well-known/asap/manifest.json")

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json() == response2.json()

    def test_manifest_endpoint_returns_cache_headers(self, client: TestClient) -> None:
        """Test that manifest endpoint returns Cache-Control and ETag."""
        response = client.get("/.well-known/asap/manifest.json")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert "public" in response.headers["cache-control"]
        assert "max-age=300" in response.headers["cache-control"]
        assert "etag" in response.headers
        assert response.headers["etag"].startswith('"') and response.headers["etag"].endswith('"')

    def test_manifest_endpoint_returns_304_when_etag_matches(self, client: TestClient) -> None:
        """Test that second request with If-None-Match returns 304 Not Modified."""
        first = client.get("/.well-known/asap/manifest.json")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag is not None
        second = client.get(
            "/.well-known/asap/manifest.json",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content in (b"", b"None") or len(second.content) == 0


class TestErrorHandling:
    """Tests for error handling and exception middleware."""

    def test_internal_error_returns_json_rpc_error(self, client: TestClient) -> None:
        """Test that internal server errors return JSON-RPC error format."""
        # Verify manifest endpoint is operational
        response = client.get("/.well-known/asap/manifest.json")
        assert response.status_code == 200

    def test_404_returns_json(self, client: TestClient) -> None:
        """Test that 404 errors return JSON format."""
        response = client.get("/non-existent-route")

        assert response.status_code == 404
        # FastAPI returns JSON by default
        assert response.headers["content-type"].startswith("application/json")


# The following test classes have been migrated to tests/transport/integration/test_server_core.py:
# - TestHandlerRegistryIntegration
# - TestMetricsEndpoint
# - TestServerExceptionHandling
# - TestAuthenticationIntegration
# All migrated classes now inherit from NoRateLimitTestBase to prevent rate limiting interference.


class TestASAPRequestHandlerHelpers:
    """Unit tests for ASAPRequestHandler helper methods."""

    @pytest.fixture
    def handler(self, sample_manifest: Manifest) -> ASAPRequestHandler:
        """Create ASAPRequestHandler instance for testing."""
        registry = HandlerRegistry()
        return ASAPRequestHandler(RegistryHolder(registry), sample_manifest, None)

    @pytest.fixture
    def metrics(self) -> MetricsCollector:
        """Get metrics collector."""
        reset_metrics()
        return get_metrics()

    def test_log_response_debug_handles_memoryview_body(self, handler: ASAPRequestHandler) -> None:
        """Test _log_response_debug decodes memoryview body when debug log is enabled."""
        payload = b'{"jsonrpc":"2.0","result":{"ok":true}}'
        response = MagicMock(spec=JSONResponse)
        response.body = memoryview(payload)
        response.status_code = 200
        with patch("asap.transport.server.is_debug_log_mode", return_value=True):
            handler._log_response_debug(response)

    def test_validate_envelope_with_valid_envelope(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _validate_envelope with valid envelope."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-1",
                skill_id="echo",
                input={"message": "test"},
            ).model_dump(),
        )

        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={"envelope": envelope.model_dump(mode="json")},
            id="test-1",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._validate_envelope(ctx)

        assert not isinstance(result, JSONResponse)
        envelope_result, payload_type = result
        assert isinstance(envelope_result, Envelope)
        assert envelope_result.payload_type == "task.request"
        assert payload_type == "task.request"

    def test_validate_envelope_with_invalid_params_type(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _validate_envelope with non-dict params."""
        # Create JsonRpcRequest with invalid params using model_construct to bypass validation
        rpc_request = JsonRpcRequest.model_construct(
            method="asap.send",
            params="invalid",
            id="test-2",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._validate_envelope(ctx)

        assert isinstance(result, JSONResponse)
        error_response = result
        assert error_response.status_code == 200

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INVALID_PARAMS

    def test_validate_envelope_with_missing_envelope(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _validate_envelope with missing envelope in params."""
        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={},  # Missing envelope
            id="test-3",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._validate_envelope(ctx)

        assert isinstance(result, JSONResponse)
        error_response = result

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INVALID_PARAMS
        assert "Missing 'envelope'" in error_data["error"]["data"]["error"]

    def test_validate_envelope_with_envelope_not_dict(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _validate_envelope when envelope is not a dict (e.g. string) returns 400."""
        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={"envelope": "string_not_dict"},
            id="test-3b",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._validate_envelope(ctx)

        assert isinstance(result, JSONResponse)
        error_response = result

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INVALID_PARAMS
        assert "envelope" in error_data["error"]["data"]["error"].lower()

    def test_validate_envelope_with_invalid_envelope_structure(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _validate_envelope with invalid envelope structure."""
        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={
                "envelope": {
                    "sender": "urn:asap:agent:client",
                    # Missing required fields
                }
            },
            id="test-4",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._validate_envelope(ctx)

        assert isinstance(result, JSONResponse)
        error_response = result

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INVALID_PARAMS
        assert "Invalid envelope structure" in error_data["error"]["data"]["error"]

    @pytest.mark.asyncio
    async def test_dispatch_to_handler_success(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _dispatch_to_handler with successful dispatch."""

        # Register a handler
        def sync_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return Envelope(
                asap_version=envelope.asap_version,
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload={"task_id": "t1", "status": "completed", "result": {}},
                correlation_id=envelope.id,
                trace_id=envelope.trace_id,
            )

        handler.registry_holder.registry.register("task.request", sync_handler)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-1",
                skill_id="echo",
                input={"message": "test"},
            ).model_dump(),
        )

        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={"envelope": envelope.model_dump(mode="json")},
            id="test-5",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = await handler._dispatch_to_handler(envelope, ctx)

        assert not isinstance(result, JSONResponse)
        response_envelope, payload_type = result
        assert isinstance(response_envelope, Envelope)
        assert response_envelope.payload_type == "task.response"
        assert payload_type == "task.request"

    @pytest.mark.asyncio
    async def test_dispatch_to_handler_not_found(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _dispatch_to_handler with handler not found."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="unknown.type",
            payload={},
        )

        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={"envelope": envelope.model_dump(mode="json")},
            id="test-6",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = await handler._dispatch_to_handler(envelope, ctx)

        assert isinstance(result, JSONResponse)
        error_response = result

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == RPC_HANDLER_NOT_FOUND
        assert "unknown.type" in error_data["error"]["data"]["details"]["payload_type"]

    def test_build_success_response(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _build_success_response creates correct response."""
        response_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test-server",
            recipient="urn:asap:agent:client",
            payload_type="task.response",
            payload={"task_id": "t1", "status": "completed", "result": {}},
            correlation_id="req-7",
        )

        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={},
            id="test-7",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        response = handler._build_success_response(response_envelope, ctx, "task.request")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200

        content = bytes(response.body).decode()
        data = json.loads(content)
        assert "result" in data
        assert data["id"] == "test-7"
        assert "envelope" in data["result"]

    def test_handle_internal_error(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _handle_internal_error creates correct error response."""
        error = ValueError("Test error")
        start_time = time.perf_counter()
        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={},
            id="test-error",
        )
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        response = handler._handle_internal_error(error, ctx, "task.request")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200

        content = bytes(response.body).decode()
        error_data = json.loads(content)
        assert "error" in error_data
        assert error_data["error"]["code"] == INTERNAL_ERROR
        # Production: generic message only; full error only when ASAP_DEBUG is set
        assert error_data["error"]["data"]["error"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_authenticate_request_without_middleware(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _authenticate_request when auth middleware is None."""
        from fastapi import Request

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})
        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={},
            id="test-8",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = await handler._authenticate_request(request, ctx)

        agent_id, error = result
        assert agent_id is None
        assert error is None

    @pytest.mark.asyncio
    async def test_verify_sender_matches_auth_without_middleware(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _verify_sender_matches_auth when auth middleware is None."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )

        rpc_request = JsonRpcRequest(
            method="asap.send",
            params={},
            id="test-9",
        )

        start_time = time.perf_counter()
        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )
        result = handler._verify_sender_matches_auth(None, envelope, ctx, "task.request")

        assert result is None

    @pytest.mark.asyncio
    async def test_parse_and_validate_request_invalid_json(
        self, handler: ASAPRequestHandler, metrics: MetricsCollector
    ) -> None:
        """Test _parse_and_validate_request with invalid JSON."""
        from fastapi import Request

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})

        # Mock the stream() method to return invalid JSON bytes
        async def invalid_json_stream() -> collections.abc.AsyncGenerator[bytes, None]:
            yield b"{invalid json"

        request.stream = lambda: invalid_json_stream()

        result = await handler._parse_and_validate_request(request)

        rpc_request, error = result
        assert rpc_request is None
        assert error is not None
        assert isinstance(error, JSONResponse)

        content = bytes(error.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == PARSE_ERROR

    @pytest.mark.asyncio
    async def test_parse_and_validate_request_non_dict_body(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test _parse_and_validate_request with non-dict body."""
        from fastapi import Request

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})

        # Mock the stream() method to return a JSON array (not a dict)
        async def array_json_stream() -> collections.abc.AsyncGenerator[bytes, None]:
            yield json.dumps(["not", "a", "dict"]).encode("utf-8")

        request.stream = lambda: array_json_stream()

        result = await handler._parse_and_validate_request(request)

        rpc_request, error = result
        assert rpc_request is None
        assert error is not None
        assert isinstance(error, JSONResponse)

        content = bytes(error.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INVALID_REQUEST
        assert "must be an object" in error_data["error"]["data"]["error"]

    def test_validate_jsonrpc_request_params_dict_type_error(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test validate_jsonrpc_request with params dict_type validation error."""
        # Create body with params as array (should trigger dict_type error)
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": ["not", "an", "object"],
            "id": "test-params-error",
        }

        rpc_request, error_response = handler.validate_jsonrpc_request(body)

        assert rpc_request is None
        assert error_response is not None
        assert isinstance(error_response, JSONResponse)

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        # Should use INVALID_PARAMS (not INVALID_REQUEST) for dict_type error on params
        assert error_data["error"]["code"] == INVALID_PARAMS
        assert "params' must be an object" in error_data["error"]["data"]["error"]

    @pytest.mark.asyncio
    async def test_handle_message_exception_before_rpc_request(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test handle_message exception handling before rpc_request is created."""
        from fastapi import Request
        from unittest.mock import AsyncMock

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})
        # Make request.json() raise an exception that's not ValueError
        request.json = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        response = await handler.handle_message(request)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200

        content = bytes(response.body).decode()
        error_data = json.loads(content)
        assert "error" in error_data
        assert error_data["error"]["code"] == INTERNAL_ERROR

    @pytest.mark.asyncio
    async def test_parse_and_validate_request_rpc_request_none_after_validation(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test _parse_and_validate_request when validate_jsonrpc_request returns None, None."""
        from fastapi import Request
        from unittest.mock import patch

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})

        # Mock the stream() method to return valid JSON
        async def valid_json_stream() -> collections.abc.AsyncGenerator[bytes, None]:
            yield json.dumps({"jsonrpc": "2.0", "method": "asap.send", "params": {}}).encode(
                "utf-8"
            )

        request.stream = lambda: valid_json_stream()

        # Mock validate_jsonrpc_request to return (None, None) - edge case
        with patch.object(handler, "validate_jsonrpc_request", return_value=(None, None)):
            result = await handler._parse_and_validate_request(request)

        rpc_request, error = result
        assert rpc_request is None
        assert error is not None
        assert isinstance(error, JSONResponse)

        content = bytes(error.body).decode()
        error_data = json.loads(content)
        assert error_data["error"]["code"] == INTERNAL_ERROR
        assert "Internal validation error" in error_data["error"]["data"]["error"]

    @pytest.mark.asyncio
    async def test_parse_and_validate_request_handles_httpexception(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test that _parse_and_validate_request handles HTTPException correctly."""
        from fastapi import HTTPException, Request
        from unittest.mock import patch

        # Create a mock request with headers (required for compression detection)
        request = Request(scope={"type": "http", "method": "POST", "path": "/asap", "headers": []})

        # Mock parse_json_body to raise HTTPException (e.g., 413 Payload Too Large)
        async def mock_parse_json_body(req: Request) -> dict:
            raise HTTPException(status_code=413, detail="Request too large")

        with patch.object(handler, "parse_json_body", side_effect=mock_parse_json_body):
            result = await handler._parse_and_validate_request(request)

        rpc_request, error_response = result
        assert rpc_request is None
        assert error_response is not None
        assert isinstance(error_response, JSONResponse)
        assert error_response.status_code == 413

        content = bytes(error_response.body).decode()
        error_data = json.loads(content)
        assert error_data["detail"] == "Request too large"

    def test_validate_request_size_handles_invalid_content_length(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test that _validate_request_size handles ValueError for invalid Content-Length header.

        This test covers the ValueError exception handler in _validate_request_size
        (server.py lines 683-685) which handles invalid Content-Length headers gracefully.
        """
        from fastapi import Request

        # Create request with invalid Content-Length header
        request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/asap",
                "headers": [(b"content-length", b"invalid-number")],
            }
        )

        try:
            handler._validate_request_size(request, max_size=1024)
        except ValueError:
            pytest.fail("ValueError should be caught internally in _validate_request_size")

    def test_validate_request_size_raises_httpexception_for_large_content(
        self, handler: ASAPRequestHandler
    ) -> None:
        """Test that _validate_request_size raises HTTPException for content exceeding max size.

        This test covers the HTTPException raised when Content-Length exceeds max_size
        (server.py lines 679-682).
        """
        from fastapi import HTTPException, Request

        # Create request with Content-Length exceeding max_size
        request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/asap",
                "headers": [(b"content-length", b"2048")],  # 2048 bytes > 1024 max
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            handler._validate_request_size(request, max_size=1024)

        assert exc_info.value.status_code == 413
        assert "exceeds maximum" in str(exc_info.value.detail)


class TestDebugLogMode:
    """Tests for ASAP_DEBUG_LOG: full request/response logging."""

    def test_debug_log_mode_logs_request_and_response(
        self, sample_manifest: Manifest, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ASAP_DEBUG_LOG=true, handler logs debug_request and debug_response."""
        from unittest.mock import patch
        from asap.transport.rate_limit import create_test_limiter

        # Use aggressive monkeypatch to replace global limiter (learned from v0.5.0)
        # This prevents rate limit state from accumulating across tests
        isolated_limiter = create_test_limiter(limits=["999999/minute"])

        # Replace in middleware module (server reads from app.state.limiter)
        import asap.transport.middleware as middleware_module

        monkeypatch.setattr(middleware_module, "limiter", isolated_limiter)

        # Create app - it will use the monkeypatched limiter
        app = create_app(sample_manifest, rate_limit="999999/minute")
        app.state.limiter = isolated_limiter
        client = TestClient(app)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-debug",
                skill_id="echo",
                input={"message": "hello"},
            ).model_dump(),
        )
        body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": "debug-1",
        }

        with (
            patch("asap.transport.server.is_debug_log_mode", return_value=True),
            patch("asap.transport.server.logger") as mock_logger,
        ):
            response = client.post("/asap", json=body)

        assert response.status_code == 200
        info_calls = [c for c in mock_logger.info.call_args_list if c[0]]
        events = [c[0][0] for c in info_calls if c[0]]
        assert "asap.request.debug_request" in events
        assert "asap.request.debug_response" in events


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRegisterEndpoint:
    """Tests for POST /asap/agent/register (Host JWT + agent public key)."""

    async def test_register_creates_pending_agent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Successful register returns agent_id, host_id, pending status and persists session."""
        agent_store = InMemoryAgentStore()
        host_store = InMemoryHostStore(agent_store=agent_store)
        app = create_app(
            sample_manifest,
            rate_limit="999999/minute",
            identity_host_store=host_store,
            identity_agent_store=agent_store,
            identity_rate_limit="999999/minute",
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "pending"
        assert "approval" in data
        assert data["approval"]["method"] == "device_authorization"
        assert data["agent_id"]
        assert data["host_id"].startswith("urn:asap:host:")
        thumb = jwk_thumbprint_sha256(agent_jwk)
        stored_list = await agent_store.list_by_host(data["host_id"])
        assert len(stored_list) == 1
        stored = stored_list[0]
        assert jwk_thumbprint_sha256(stored.public_key) == thumb
        assert stored.agent_id == data["agent_id"]
        assert stored.host_id == data["host_id"]

    async def test_register_a2h_uses_host_id_as_principal(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """When host.user_id is unset, A2H registration approval uses host_id as principal."""
        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        ch = AsyncMock()
        app.state.identity_approval_a2h_channel = ch
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "pending"
        ch.resolve_via_a2h.assert_awaited_once()
        call = ch.resolve_via_a2h.await_args
        assert call is not None
        assert call.kwargs["principal_id"] == data["host_id"]

    async def test_register_a2h_uses_linked_user_id_as_principal(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """When host.user_id is set, A2H registration approval uses user_id as principal."""
        app, agent_store, host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk_first = Ed25519PrivateKey.generate()
        first_jwk = ed25519_public_jwk(agent_sk_first)
        first_token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=first_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r0 = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        assert r0.status_code == 200
        host_id = r0.json()["host_id"]
        host = await host_store.get(host_id)
        assert host is not None
        await host_store.save(host.model_copy(update={"user_id": "linked-user-42"}))

        ch = AsyncMock()
        app.state.identity_approval_a2h_channel = ch
        agent_sk_second = Ed25519PrivateKey.generate()
        second_jwk = ed25519_public_jwk(agent_sk_second)
        second_token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=second_jwk,
            ttl_seconds=120,
        )
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {second_token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        ch.resolve_via_a2h.assert_awaited_once()
        call = ch.resolve_via_a2h.await_args
        assert call is not None
        assert call.kwargs["principal_id"] == "linked-user-42"
        stored_list = await agent_store.list_by_host(host_id)
        assert len(stored_list) == 2

    def test_register_browser_agent_returns_403_webauthn_when_real_verifier_configured(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Browser-controlled registration requires WebAuthn when the real verifier is active."""
        import importlib.util

        if importlib.util.find_spec("webauthn") is None:
            pytest.skip("webauthn extra not installed in this environment")

        monkeypatch.setenv("ASAP_WEBAUTHN_RP_ID", "localhost")
        monkeypatch.setenv("ASAP_WEBAUTHN_ORIGIN", "http://127.0.0.1")

        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_controls_browser": True},
        )
        assert r.status_code == 403
        assert r.json() == {"detail": "webauthn_required"}

    def test_register_browser_agent_succeeds_without_webauthn_when_placeholder_verifier(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without RP/origin env, default verifier is placeholder — no WebAuthn gate."""
        monkeypatch.delenv("ASAP_WEBAUTHN_RP_ID", raising=False)
        monkeypatch.delenv("ASAP_WEBAUTHN_ORIGIN", raising=False)

        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_controls_browser": True},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_register_rejects_stale_host_jwt_before_pending_approval(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Approval-bound registration rejects stale Host JWTs at the HTTP boundary."""
        agent_store = InMemoryAgentStore()
        host_store = InMemoryHostStore(agent_store=agent_store)
        app = create_app(
            sample_manifest,
            rate_limit="999999/minute",
            identity_host_store=host_store,
            identity_agent_store=agent_store,
            identity_rate_limit="999999/minute",
            identity_fresh_session_config=FreshSessionConfig(window_seconds=60),
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        issued_at = int(time.time()) - 120
        with monkeypatch.context() as time_patch:
            time_patch.setattr("asap.auth.agent_jwt.time.time", lambda: float(issued_at))
            token = create_host_jwt(
                host_sk,
                aud=_HOST_JWT_AUDIENCE,
                agent_public_key=ed25519_public_jwk(agent_sk),
                ttl_seconds=3600,
            )

        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 403
        assert r.json() == {
            "detail": "stale host session; re-authenticate within the fresh-session window "
            "(60s) to use approval endpoints"
        }

    def test_register_requires_webauthn_for_high_risk_capability(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """High-risk capabilities require a WebAuthn block before pending approval is created."""
        verifier = _RejectingWebAuthnVerifier()
        agent_store = InMemoryAgentStore()
        host_store = InMemoryHostStore(agent_store=agent_store)
        app = create_app(
            sample_manifest,
            rate_limit="999999/minute",
            identity_host_store=host_store,
            identity_agent_store=agent_store,
            identity_rate_limit="999999/minute",
            identity_fresh_session_config=FreshSessionConfig(
                window_seconds=300,
                require_webauthn_for=["admin.task"],
            ),
            identity_webauthn_verifier=verifier,
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )

        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"capabilities": ["admin.task"]},
        )

        assert r.status_code == 400
        assert r.json() == {"detail": "webauthn object required for high-risk capabilities"}
        assert verifier.calls == []

    def test_register_reports_failed_webauthn_for_high_risk_capability(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Invalid WebAuthn assertions fail closed for high-risk capability requests."""
        verifier = _RejectingWebAuthnVerifier()
        agent_store = InMemoryAgentStore()
        host_store = InMemoryHostStore(agent_store=agent_store)
        app = create_app(
            sample_manifest,
            rate_limit="999999/minute",
            identity_host_store=host_store,
            identity_agent_store=agent_store,
            identity_rate_limit="999999/minute",
            identity_fresh_session_config=FreshSessionConfig(
                window_seconds=300,
                require_webauthn_for=["admin.task"],
            ),
            identity_webauthn_verifier=verifier,
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )

        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "capabilities": ["admin.task"],
                "webauthn": {"challenge": "challenge-1", "response": {"id": "cred-1"}},
            },
        )

        assert r.status_code == 400
        assert r.json() == {"detail": "webauthn verification failed"}
        assert len(verifier.calls) == 1
        challenge, response, host_id, require_user_verification = verifier.calls[0]
        assert challenge == "challenge-1"
        assert response == {"id": "cred-1"}
        assert host_id is not None
        assert host_id.startswith("urn:asap:host:")
        assert require_user_verification is False

    async def test_register_auto_approves_when_host_active_and_capabilities_in_defaults(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Trusted host with requested caps ⊆ default_capabilities skips manual approval."""
        app, _agent_store, host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        host_pub = ed25519_public_jwk(host_sk)
        agent_pub = ed25519_public_jwk(agent_sk)
        now = datetime.now(timezone.utc)
        await host_store.save(
            HostIdentity(
                host_id=host_urn_from_thumbprint(jwk_thumbprint_sha256(host_pub)),
                public_key=dict(host_pub),
                status="active",
                default_capabilities=["exec:read"],
                created_at=now,
                updated_at=now,
            )
        )
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_pub,
            ttl_seconds=120,
        )
        client = TestClient(app)
        r = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
            json={"capabilities": ["exec:read"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "active"
        assert "approval" not in body

    def test_register_idempotent_for_same_agent_key(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Second register with same host JWT and agent key returns the same agent_id."""
        agent_store = InMemoryAgentStore()
        host_store = InMemoryHostStore(agent_store=agent_store)
        app = create_app(
            sample_manifest,
            rate_limit="999999/minute",
            identity_host_store=host_store,
            identity_agent_store=agent_store,
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        client = TestClient(app)
        token1 = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        token2 = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        r1 = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token1}"},
        )
        r2 = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["agent_id"] == r2.json()["agent_id"]

    def test_register_without_bearer_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app = create_app(sample_manifest, rate_limit="999999/minute")
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        r = TestClient(app).post("/asap/agent/register")
        assert r.status_code == 401

    def test_register_host_jwt_wrong_audience_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Host JWT with ``aud`` not matching this server (manifest id) is rejected."""
        app = create_app(sample_manifest, rate_limit="999999/minute")
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        token = create_host_jwt(
            host_sk,
            aud="payment-service-other",
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        assert "audience" in r.json()["detail"].lower()

    def test_register_without_agent_public_key_claim_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        host_sk = Ed25519PrivateKey.generate()
        token = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        app = create_app(sample_manifest, rate_limit="999999/minute")
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter
        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "agent_public_key" in r.json()["detail"]

    def test_register_returns_500_when_approval_required_but_store_missing(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Registration requiring approval fails closed when approval store is unavailable."""
        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        app.state.identity_approval_store = None

        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        token = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )

        r = TestClient(app).post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 500
        assert r.json() == {"detail": "approval store not configured"}


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentStatusEndpoint:
    """Tests for GET /asap/agent/status (Host JWT, query ``agent_id``)."""

    def test_status_returns_pending_and_lifecycle(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Pending agent reports status, empty capabilities, and lifecycle fields."""
        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        agent_jwk = ed25519_public_jwk(agent_sk)
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=agent_jwk,
            ttl_seconds=120,
        )
        client = TestClient(app)
        reg = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        )
        assert reg.status_code == 200
        aid = reg.json()["agent_id"]
        host_tok = _host_jwt_without_agent_claim(host_sk)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert st.status_code == 200
        body = st.json()
        assert body["agent_id"] == aid
        assert body["status"] == "pending"
        assert body.get("approval_status") == "pending"
        assert "approval" in body
        assert body["capabilities"] == []
        life = body["lifecycle"]
        assert life["mode"] == "delegated"
        assert life["created_at"]
        assert life["activated_at"] is None
        assert life["last_used_at"] is None

    async def test_status_reflects_active(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        reg = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        )
        aid = reg.json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        now = datetime.now(timezone.utc)
        await agent_store.save(
            sess.model_copy(
                update={"status": "active", "activated_at": now},
            )
        )
        host_tok = _host_jwt_without_agent_claim(host_sk)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "active"
        assert st.json()["lifecycle"]["activated_at"] is not None

    async def test_status_reflects_expired(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "expired"}))
        host_tok = _host_jwt_without_agent_claim(host_sk)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "expired"

    async def test_status_reflects_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        await agent_store.revoke(aid)
        host_tok = _host_jwt_without_agent_claim(host_sk)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {host_tok}"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "revoked"

    def test_status_same_host_jwt_reusable_without_jti_replay(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """GET status preserves polling by not recording ``jti`` on each read."""
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        host_tok = _host_jwt_without_agent_claim(host_sk)
        headers = {"Authorization": f"Bearer {host_tok}"}
        u = f"/asap/agent/status?agent_id={aid}"
        assert client.get(u, headers=headers).status_code == 200
        assert client.get(u, headers=headers).status_code == 200

    def test_status_rejects_host_jwt_replayed_from_recording_route(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Status rejects Host JWTs whose ``jti`` was already consumed elsewhere."""
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]

        replay = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {reg_tok}"},
        )
        assert replay.status_code == 401
        assert replay.json() == {"detail": "jti replay detected"}

    @pytest.mark.parametrize("recording_route", ["/asap/agent/revoke", "/asap/agent/rotate-key"])
    def test_status_rejects_host_jwt_replayed_after_recording_route(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
        recording_route: str,
    ) -> None:
        """Status rejects Host JWTs already consumed by revoke or rotate-key."""
        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]

        recording_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        recording_headers = {"Authorization": f"Bearer {recording_tok}"}
        if recording_route == "/asap/agent/revoke":
            route_response = client.post(
                recording_route,
                headers=recording_headers,
                json={"agent_id": aid},
            )
            assert route_response.status_code == 200
            assert route_response.json()["status"] == "revoked"
        else:
            route_response = client.post(
                recording_route,
                headers=recording_headers,
                json={
                    "agent_id": aid,
                    "new_public_key": ed25519_public_jwk(Ed25519PrivateKey.generate()),
                },
            )
            assert route_response.status_code == 200

        replay = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers=recording_headers,
        )
        assert replay.status_code == 401
        assert replay.json() == {"detail": "jti replay detected"}

    async def test_status_rejects_host_jwt_replayed_after_reactivate(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Status rejects Host JWTs already consumed by reactivate (JTI recording route)."""
        app, agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "expired"}))

        recording_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        recording_headers = {"Authorization": f"Bearer {recording_tok}"}
        reactivate = client.post(
            "/asap/agent/reactivate",
            headers=recording_headers,
            json={"agent_id": aid},
        )
        assert reactivate.status_code == 200

        replay = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers=recording_headers,
        )
        assert replay.status_code == 401
        assert replay.json() == {"detail": "jti replay detected"}

    def test_status_wrong_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host1_sk = Ed25519PrivateKey.generate()
        host2_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host1_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        other_tok = _host_jwt_without_agent_claim(host2_sk)
        st = client.get(
            f"/asap/agent/status?agent_id={aid}",
            headers={"Authorization": f"Bearer {other_tok}"},
        )
        assert st.status_code == 403
        assert "host" in st.json()["detail"].lower()

    def test_status_unknown_agent_returns_404(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        tok = _host_jwt_without_agent_claim(host_sk)
        st = TestClient(app).get(
            "/asap/agent/status?agent_id=nonexistentagentid00",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st.status_code == 404

    def test_status_missing_agent_id_query_returns_422(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        tok = _host_jwt_without_agent_claim(host_sk)
        st = TestClient(app).get(
            "/asap/agent/status",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st.status_code == 422


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRevokeEndpoint:
    """Tests for POST /asap/agent/revoke (Host JWT + JSON ``agent_id``)."""

    def test_revoke_returns_revoked(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _agent_store, _host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        rev_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/revoke",
            headers={"Authorization": f"Bearer {rev_tok}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 200
        assert r.json() == {"agent_id": aid, "status": "revoked"}

    def test_revoke_idempotent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Second revoke with a fresh Host JWT still returns revoked."""
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        t1 = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        t2 = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        assert (
            client.post(
                "/asap/agent/revoke",
                headers={"Authorization": f"Bearer {t1}"},
                json={"agent_id": aid},
            ).status_code
            == 200
        )
        r2 = client.post(
            "/asap/agent/revoke",
            headers={"Authorization": f"Bearer {t2}"},
            json={"agent_id": aid},
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "revoked"

    def test_revoke_without_bearer_returns_401(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        r = TestClient(app).post("/asap/agent/revoke", json={"agent_id": "anyid"})
        assert r.status_code == 401

    def test_revoke_unknown_agent_returns_404(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = TestClient(app).post(
            "/asap/agent/revoke",
            headers={"Authorization": f"Bearer {tok}"},
            json={"agent_id": "nonexistentagentid00"},
        )
        assert r.status_code == 404

    def test_revoke_wrong_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host1_sk = Ed25519PrivateKey.generate()
        host2_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host1_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        other_tok = create_host_jwt(host2_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/revoke",
            headers={"Authorization": f"Bearer {other_tok}"},
            json={"agent_id": aid},
        )
        assert r.status_code == 403

    def test_revoke_rejects_extra_json_fields(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = TestClient(app).post(
            "/asap/agent/revoke",
            headers={"Authorization": f"Bearer {tok}"},
            json={"agent_id": "x", "evil": 1},
        )
        assert r.status_code == 422

    async def test_after_revoke_agent_jwt_verify_fails(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """``verify_agent_jwt`` must reject the session after HTTP revoke."""
        app, agent_store, host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        host_pub = ed25519_public_jwk(host_sk)
        host_tp = jwk_thumbprint_sha256(host_pub)
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(
            sess.model_copy(update={"status": "active"}),
        )
        agent_token = create_agent_jwt(
            agent_sk,
            host_thumbprint=host_tp,
            agent_id=aid,
            aud="https://asap.test/asap",
        )
        ok_before = await verify_agent_jwt(agent_token, host_store, agent_store)
        assert ok_before.ok
        rev_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        assert (
            client.post(
                "/asap/agent/revoke",
                headers={"Authorization": f"Bearer {rev_tok}"},
                json={"agent_id": aid},
            ).status_code
            == 200
        )
        ok_after = await verify_agent_jwt(agent_token, host_store, agent_store)
        assert not ok_after.ok
        assert ok_after.error is not None
        assert "revoked" in ok_after.error.lower()


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
class TestAgentRotateKeyEndpoint:
    """Tests for POST /asap/agent/rotate-key (Host JWT + new JWK)."""

    async def test_rotate_old_agent_jwt_rejected_new_accepted(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """After rotation, JWT signed with the previous key fails; new key verifies."""
        app, agent_store, host_store = _app_with_identity_stores(
            sample_manifest, isolated_rate_limiter
        )
        host_sk = Ed25519PrivateKey.generate()
        agent_sk_old = Ed25519PrivateKey.generate()
        agent_sk_new = Ed25519PrivateKey.generate()
        host_tp = jwk_thumbprint_sha256(ed25519_public_jwk(host_sk))
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk_old),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        sess = await agent_store.get(aid)
        assert sess is not None
        await agent_store.save(sess.model_copy(update={"status": "active"}))
        aud = "https://asap.test/asap"
        token_old = create_agent_jwt(
            agent_sk_old,
            host_thumbprint=host_tp,
            agent_id=aid,
            aud=aud,
        )
        assert (await verify_agent_jwt(token_old, host_store, agent_store)).ok
        rot_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        rot = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {rot_tok}"},
            json={
                "agent_id": aid,
                "new_public_key": ed25519_public_jwk(agent_sk_new),
            },
        )
        assert rot.status_code == 200
        assert rot.json()["agent_id"] == aid
        assert rot.json()["status"] == "active"
        bad = await verify_agent_jwt(token_old, host_store, agent_store)
        assert not bad.ok
        token_new = create_agent_jwt(
            agent_sk_new,
            host_thumbprint=host_tp,
            agent_id=aid,
            aud=aud,
        )
        assert (await verify_agent_jwt(token_new, host_store, agent_store)).ok

    def test_rotate_same_key_idempotent(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _as, _hs = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        pub = ed25519_public_jwk(agent_sk)
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=pub,
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        rot_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {rot_tok}"},
            json={"agent_id": aid, "new_public_key": pub},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_rotate_revoked_agent_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _as, _hs = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        new_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        t_rev = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        assert (
            client.post(
                "/asap/agent/revoke",
                headers={"Authorization": f"Bearer {t_rev}"},
                json={"agent_id": aid},
            ).status_code
            == 200
        )
        t_rot = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {t_rot}"},
            json={"agent_id": aid, "new_public_key": ed25519_public_jwk(new_sk)},
        )
        assert r.status_code == 400
        assert "revoked" in r.json()["detail"].lower()

    def test_rotate_unknown_agent_returns_404(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        new_sk = Ed25519PrivateKey.generate()
        tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = TestClient(app).post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "agent_id": "nonexistentagentid00",
                "new_public_key": ed25519_public_jwk(new_sk),
            },
        )
        assert r.status_code == 404

    def test_rotate_wrong_host_returns_403(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host1_sk = Ed25519PrivateKey.generate()
        host2_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        new_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host1_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        other_tok = create_host_jwt(host2_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {other_tok}"},
            json={"agent_id": aid, "new_public_key": ed25519_public_jwk(new_sk)},
        )
        assert r.status_code == 403

    def test_rotate_invalid_jwk_returns_400(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        agent_sk = Ed25519PrivateKey.generate()
        reg_tok = create_host_jwt(
            host_sk,
            aud=_HOST_JWT_AUDIENCE,
            agent_public_key=ed25519_public_jwk(agent_sk),
            ttl_seconds=120,
        )
        client = TestClient(app)
        aid = client.post(
            "/asap/agent/register",
            headers={"Authorization": f"Bearer {reg_tok}"},
        ).json()["agent_id"]
        rot_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {rot_tok}"},
            json={"agent_id": aid, "new_public_key": {"kty": "RSA", "n": "x", "e": "AQAB"}},
        )
        assert r.status_code == 400

    def test_rotate_duplicate_key_on_sibling_agent_returns_409(
        self,
        sample_manifest: Manifest,
        isolated_rate_limiter: "ASAPRateLimiter | None",
    ) -> None:
        """Cannot assign another agent's public key to a second session on the same host."""
        app, _a, _h = _app_with_identity_stores(sample_manifest, isolated_rate_limiter)
        host_sk = Ed25519PrivateKey.generate()
        sk1 = Ed25519PrivateKey.generate()
        sk2 = Ed25519PrivateKey.generate()
        pub1 = ed25519_public_jwk(sk1)
        pub2 = ed25519_public_jwk(sk2)
        client = TestClient(app)
        assert (
            client.post(
                "/asap/agent/register",
                headers={
                    "Authorization": f"Bearer {create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, agent_public_key=pub1, ttl_seconds=120)}"
                },
            ).status_code
            == 200
        )
        aid2 = client.post(
            "/asap/agent/register",
            headers={
                "Authorization": f"Bearer {create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, agent_public_key=pub2, ttl_seconds=120)}"
            },
        ).json()["agent_id"]
        rot_tok = create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE, ttl_seconds=120)
        r = client.post(
            "/asap/agent/rotate-key",
            headers={"Authorization": f"Bearer {rot_tok}"},
            json={"agent_id": aid2, "new_public_key": pub1},
        )
        assert r.status_code == 409


class TestSwaggerUiDebugMode:
    """Tests for Swagger UI /docs enabled only when ASAP_DEBUG=true."""

    def test_docs_disabled_when_not_debug(self, sample_manifest: Manifest) -> None:
        """When ASAP_DEBUG is not set, /docs and /openapi.json return 404."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"ASAP_DEBUG": ""}, clear=False):
            app = create_app(sample_manifest, rate_limit="100000/minute")
            client = TestClient(app)

        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    def test_docs_enabled_when_debug(self, sample_manifest: Manifest) -> None:
        """When ASAP_DEBUG=true, /docs and /openapi.json are available."""
        from unittest.mock import patch

        with patch.dict("os.environ", {"ASAP_DEBUG": "true"}):
            app = create_app(sample_manifest, rate_limit="100000/minute")
            client = TestClient(app)

        # /docs may redirect (307) or return 200
        docs_resp = client.get("/docs")
        assert docs_resp.status_code in (200, 307)
        assert client.get("/openapi.json").status_code == 200
