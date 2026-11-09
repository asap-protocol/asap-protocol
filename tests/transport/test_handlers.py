"""Tests for ASAP protocol handler registry.

This module tests the HandlerRegistry class that manages payload-type-specific
handlers for processing ASAP envelopes.
"""

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest

from asap.errors import ASAPError
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.enums import TaskStatus
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse
from asap.testing import assert_envelope_valid, assert_response_correlates

if TYPE_CHECKING:
    pass


class TestValidateHandler:
    """Tests for validate_handler helper."""

    def test_validate_handler_accepts_two_param_function(self) -> None:
        """Test that validate_handler accepts (envelope, manifest) signature."""
        from asap.transport.handlers import validate_handler

        def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        validate_handler(handler)  # No raise

    def test_validate_handler_accepts_three_param_with_self(self) -> None:
        """Test that validate_handler accepts (self, envelope, manifest) for callable class."""
        from asap.transport.handlers import validate_handler

        class HandlerClass:
            def __call__(self, envelope: Envelope, manifest: Manifest) -> Envelope:
                return envelope

        validate_handler(HandlerClass())  # No raise

    def test_register_accepts_unbound_self_and_cls_parameters(self) -> None:
        """Unbound callables keep ``self`` or ``cls`` in ``inspect.signature``.

        A bound ``HandlerClass()`` drops ``self``, so removing either name from
        the allow-list still passes ``test_validate_handler_accepts_three_param_with_self``.
        """
        from asap.transport.handlers import HandlerRegistry

        def with_self(self: object, envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        def with_cls(cls: object, envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        registry = HandlerRegistry()
        registry.register("task.request", with_self)
        registry.register("task.response", with_cls)
        assert registry.has_handler("task.request") is True
        assert registry.has_handler("task.response") is True

    def test_register_rejects_three_parameters_without_self_or_cls(self) -> None:
        """Exact ``self`` and ``cls`` names are the only three-parameter allow-list.

        ``receiver`` misses a prefix check. ``selfish`` and ``cls_extra`` stay
        registered if the allow-list uses ``startswith`` or containment.
        """
        from asap.transport.handlers import HandlerRegistry

        def receiver(receiver: object, envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        def selfish(selfish: object, envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        def cls_extra(cls_extra: object, envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        for candidate in (receiver, selfish, cls_extra):
            registry = HandlerRegistry()
            with pytest.raises(TypeError, match="3 parameters"):
                registry.register("task.request", candidate)
            assert registry.has_handler("task.request") is False

    def test_validate_handler_rejects_non_callable(self) -> None:
        """Test that validate_handler rejects non-callable."""
        from asap.transport.handlers import validate_handler

        with pytest.raises(TypeError) as exc_info:
            validate_handler(cast(Any, "not a handler"))
        assert "callable" in exc_info.value.args[0].lower()

    def test_validate_handler_rejects_wrong_parameter_count(self) -> None:
        """Test that validate_handler rejects wrong number of parameters."""
        from asap.transport.handlers import validate_handler

        def bad_one(envelope: Envelope) -> Envelope:
            return envelope

        with pytest.raises(TypeError) as exc_info:
            validate_handler(cast(Any, bad_one))
        assert "2 parameters" in exc_info.value.args[0] or "envelope" in exc_info.value.args[0]

        def bad_four(envelope: Envelope, manifest: Manifest, a: int, b: int) -> Envelope:
            return envelope

        with pytest.raises(TypeError) as exc_info:
            validate_handler(cast(Any, bad_four))
        assert "4 parameters" in exc_info.value.args[0]

    def test_validate_handler_rejects_when_signature_inspection_fails(
        self,
    ) -> None:
        """Test that validate_handler raises when inspect.signature fails."""
        from asap.transport.handlers import validate_handler

        def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        with (
            patch.object(inspect, "signature", side_effect=ValueError("unsupported callable")),
            pytest.raises(TypeError) as exc_info,
        ):
            validate_handler(handler)
        assert "could not be inspected" in exc_info.value.args[0].lower()


# Test fixtures
@pytest.fixture
def sample_manifest() -> Manifest:
    """Create a sample manifest for testing."""
    return Manifest(
        id="urn:asap:agent:test-handler",
        name="Test Handler Agent",
        version="1.0.0",
        description="Agent for testing handlers",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo skill")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


@pytest.fixture
def sample_task_request_envelope() -> Envelope:
    """Create a sample TaskRequest envelope for testing."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv_123",
            skill_id="echo",
            input={"message": "Hello, world!"},
        ).model_dump(),
    )


@pytest.fixture
def sample_task_cancel_envelope() -> Envelope:
    """Create a sample TaskCancel envelope for testing."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type="task.cancel",
        payload={"task_id": "task_123", "reason": "User requested"},
    )


class TestHandlerRegistry:
    """Tests for HandlerRegistry class."""

    def test_create_empty_registry(self) -> None:
        """Test creating an empty handler registry."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        assert registry is not None

    def test_register_handler(self) -> None:
        """Test registering a handler for a payload type."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def mock_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        registry.register("task.request", mock_handler)

        # Handler should be registered
        assert registry.has_handler("task.request")

    def test_register_multiple_handlers(self) -> None:
        """Test registering multiple handlers for different payload types."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def handler1(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        def handler2(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        registry.register("task.request", handler1)
        registry.register("task.cancel", handler2)

        assert registry.has_handler("task.request")
        assert registry.has_handler("task.cancel")
        assert not registry.has_handler("task.update")

    def test_has_handler_returns_false_for_unregistered(self) -> None:
        """Test has_handler returns False for unregistered payload type."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        assert not registry.has_handler("unknown.type")

    def test_dispatch_to_registered_handler(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test dispatching envelope to registered handler."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        # Create a response envelope for the handler to return
        response_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
            payload_type="task.response",
            payload=TaskResponse(
                task_id="task_123",
                status=TaskStatus.COMPLETED,
                result={"echoed": "Hello, world!"},
            ).model_dump(),
            correlation_id=sample_task_request_envelope.id,
        )

        def mock_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return response_envelope

        registry.register("task.request", mock_handler)

        result = registry.dispatch(sample_task_request_envelope, sample_manifest)

        assert result == response_envelope
        assert_envelope_valid(result, allowed_payload_types=["task.response"])
        assert_response_correlates(sample_task_request_envelope, result)

    def test_dispatch_unknown_payload_type_raises_error(self, sample_manifest: Manifest) -> None:
        """Test dispatching unknown payload type raises error."""
        from asap.transport.handlers import HandlerNotFoundError, HandlerRegistry

        registry = HandlerRegistry()

        unknown_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="unknown.type",
            payload={"data": "test"},
        )

        with pytest.raises(HandlerNotFoundError) as exc_info:
            registry.dispatch(unknown_envelope, sample_manifest)

        assert exc_info.value.payload_type == "unknown.type"
        assert "unknown.type" in str(exc_info.value)

    def test_handler_receives_correct_envelope(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test handler receives the exact envelope passed to dispatch."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        received_envelope = None
        received_manifest = None

        def capturing_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            nonlocal received_envelope, received_manifest
            received_envelope = envelope
            received_manifest = manifest
            # Return a valid response
            return Envelope(
                asap_version="0.1",
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_123",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        registry.register("task.request", capturing_handler)
        registry.dispatch(sample_task_request_envelope, sample_manifest)

        assert received_envelope == sample_task_request_envelope
        assert received_manifest == sample_manifest

    def test_list_registered_handlers(self) -> None:
        """Test listing all registered payload types."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        registry.register("task.request", handler)
        registry.register("task.cancel", handler)
        registry.register("message.send", handler)

        registered = registry.list_handlers()

        assert len(registered) == 3
        assert "task.request" in registered
        assert "task.cancel" in registered
        assert "message.send" in registered

    def test_override_existing_handler(self) -> None:
        """Test that registering a handler for existing type overrides it."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        call_count = {"handler1": 0, "handler2": 0}

        def handler1(envelope: Envelope, manifest: Manifest) -> Envelope:
            call_count["handler1"] += 1
            return envelope

        def handler2(envelope: Envelope, manifest: Manifest) -> Envelope:
            call_count["handler2"] += 1
            return Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:server",
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_123",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        registry.register("task.request", handler1)
        registry.register("task.request", handler2)  # Override

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_1",
                skill_id="test",
                input={},
            ).model_dump(),
        )

        manifest = Manifest(
            id="urn:asap:agent:test",
            name="Test",
            version="1.0.0",
            description="Test",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="test", description="Test")],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="http://localhost/asap"),
        )

        registry.dispatch(envelope, manifest)

        assert call_count["handler1"] == 0
        assert call_count["handler2"] == 1

    def test_dispatch_sync_handler_exception_is_propagated(self, sample_manifest: Manifest) -> None:
        """Test that exceptions from sync handlers are propagated and logged."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def failing_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            raise ValueError("Sync handler failed intentionally")

        registry.register("task.request", failing_handler)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_exception",
                skill_id="test",
                input={},
            ).model_dump(),
        )

        with pytest.raises(ValueError, match="Sync handler failed intentionally"):
            registry.dispatch(envelope, sample_manifest)

    def test_dispatch_sync_handler_exception_logs_error(
        self, sample_manifest: Manifest, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that exceptions from sync handlers are logged with details."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def failing_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            raise RuntimeError("Logged error test")

        registry.register("task.request", failing_handler)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_log",
                skill_id="test",
                input={},
            ).model_dump(),
        )

        with pytest.raises(RuntimeError):
            registry.dispatch(envelope, sample_manifest)

    def test_dispatch_raises_when_sync_handler_returns_awaitable(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test that sync dispatch raises when handler returns a coroutine."""
        from asap.transport.handlers import HandlerRegistry

        async def async_impl(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        def handler_returning_awaitable(envelope: Envelope, manifest: Manifest) -> "Envelope":
            return async_impl(envelope, manifest)

        registry = HandlerRegistry()
        registry.register("task.request", handler_returning_awaitable)

        with pytest.raises(TypeError) as exc_info:
            registry.dispatch(sample_task_request_envelope, sample_manifest)
        assert "awaitable" in exc_info.value.args[0].lower()
        assert "dispatch_async" in exc_info.value.args[0].lower()


class TestHandlerNotFoundError:
    """Tests for HandlerNotFoundError exception."""

    def test_error_has_correct_code(self) -> None:
        """Test error has correct ASAP error code."""
        from asap.transport.handlers import HandlerNotFoundError

        error = HandlerNotFoundError("unknown.type")

        assert error.code == "asap:transport/handler_not_found"

    def test_error_message_contains_payload_type(self) -> None:
        """Test error message contains the payload type."""
        from asap.transport.handlers import HandlerNotFoundError

        error = HandlerNotFoundError("some.payload.type")

        assert "some.payload.type" in error.message
        assert "some.payload.type" in str(error)

    def test_error_exposes_payload_type(self) -> None:
        """Test error exposes payload_type attribute."""
        from asap.transport.handlers import HandlerNotFoundError

        error = HandlerNotFoundError("task.request")

        assert error.payload_type == "task.request"

    def test_error_is_asap_error(self) -> None:
        """Test error inherits from ASAPError."""
        from asap.transport.handlers import HandlerNotFoundError

        error = HandlerNotFoundError("test.type")

        assert isinstance(error, ASAPError)

    def test_error_to_dict_serialization(self) -> None:
        """Test error can be serialized to dictionary."""
        from asap.transport.handlers import HandlerNotFoundError

        error = HandlerNotFoundError("my.payload")
        result = error.to_dict()

        from asap.errors import RPC_HANDLER_NOT_FOUND

        assert result["code"] == "asap:transport/handler_not_found"
        assert result["rpc_code"] == RPC_HANDLER_NOT_FOUND
        assert "my.payload" in result["message"]
        assert result["details"]["payload_type"] == "my.payload"


class TestTaskRequestHandler:
    """Tests for base TaskRequestHandler."""

    def test_echo_handler_returns_task_response(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test echo handler returns TaskResponse envelope."""
        from asap.transport.handlers import create_echo_handler

        handler = create_echo_handler()
        raw = handler(sample_task_request_envelope, sample_manifest)
        result = cast(Envelope, raw)

        assert result.payload_type == "task.response"
        assert result.sender == sample_manifest.id
        assert result.recipient == sample_task_request_envelope.sender
        assert result.correlation_id == sample_task_request_envelope.id

    def test_echo_handler_echoes_input(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test echo handler echoes the input data."""
        from asap.transport.handlers import create_echo_handler

        handler = create_echo_handler()
        raw = handler(sample_task_request_envelope, sample_manifest)
        result = cast(Envelope, raw)

        response_payload = TaskResponse(**result.payload_dict)
        assert response_payload.status == TaskStatus.COMPLETED
        assert response_payload.result is not None
        assert "echoed" in response_payload.result
        assert response_payload.result["echoed"] == {"message": "Hello, world!"}

    def test_echo_handler_preserves_trace_id(self, sample_manifest: Manifest) -> None:
        """Test echo handler preserves trace_id from request."""
        from asap.transport.handlers import create_echo_handler

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_123",
                skill_id="echo",
                input={"data": "test"},
            ).model_dump(),
            trace_id="trace_abc123",
        )

        handler = create_echo_handler()
        raw = handler(envelope, sample_manifest)
        result = cast(Envelope, raw)

        assert result.trace_id == "trace_abc123"

    def test_echo_handler_generates_task_id(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test echo handler generates a task_id in response."""
        from asap.transport.handlers import create_echo_handler

        handler = create_echo_handler()
        raw = handler(sample_task_request_envelope, sample_manifest)
        result = cast(Envelope, raw)

        response_payload = TaskResponse(**result.payload_dict)
        assert response_payload.task_id is not None
        assert len(response_payload.task_id) > 0

    def test_echo_handler_raises_validation_error_on_invalid_payload(
        self, sample_manifest: Manifest
    ) -> None:
        """Test echo handler raises ValidationError when payload fails Pydantic validation."""
        from types import SimpleNamespace

        from pydantic import ValidationError

        from asap.transport.handlers import create_echo_handler

        # Envelope normally validates payload on build; use a minimal object that
        # exposes invalid payload_dict (missing required skill_id) to exercise
        # Handler validates payload as TaskRequest.
        invalid_payload = {"conversation_id": "conv_1", "input": {}}
        envelope_like = SimpleNamespace(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            id="env-1",
            trace_id=None,
            payload_dict=invalid_payload,
        )
        handler = create_echo_handler()
        with pytest.raises(ValidationError) as exc_info:
            handler(envelope_like, sample_manifest)
        errors = exc_info.value.errors()
        assert any("skill_id" in str(loc) for e in errors for loc in (e.get("loc") or ())), (
            f"Expected skill_id in validation errors, got: {errors}"
        )


class TestDefaultRegistry:
    """Tests for creating a pre-configured registry with default handlers."""

    def test_create_default_registry(self) -> None:
        """Test creating registry with default handlers."""
        from asap.transport.handlers import create_default_registry

        registry = create_default_registry()

        # Should have at least task.request handler
        assert registry.has_handler("task.request")

    def test_default_registry_handles_task_request(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test default registry can handle task.request."""
        from asap.transport.handlers import create_default_registry

        registry = create_default_registry()
        result = registry.dispatch(sample_task_request_envelope, sample_manifest)

        assert result.payload_type == "task.response"


class TestHandlerRegistryThreadSafety:
    """Tests for HandlerRegistry thread safety."""

    def test_registry_has_lock(self) -> None:
        """Test registry has internal lock for thread safety."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        assert hasattr(registry, "_lock")
        assert isinstance(registry._lock, type(threading.RLock()))

    def test_concurrent_registrations(self) -> None:
        """Test concurrent handler registrations from multiple threads."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        num_threads = 10
        registrations_per_thread = 100
        errors: list[Exception] = []

        def register_handlers(thread_id: int) -> None:
            try:
                for i in range(registrations_per_thread):
                    payload_type = f"thread{thread_id}.type{i}"

                    def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
                        return envelope

                    registry.register(payload_type, handler)
            except Exception as e:
                errors.append(e)

        # Run registrations concurrently
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(register_handlers, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()  # Raise any exceptions

        # Verify no errors occurred
        assert len(errors) == 0

        # Verify all handlers were registered
        registered = registry.list_handlers()
        expected_count = num_threads * registrations_per_thread
        assert len(registered) == expected_count

    def test_concurrent_dispatch(
        self, sample_task_request_envelope: Envelope, sample_manifest: Manifest
    ) -> None:
        """Test concurrent dispatch calls from multiple threads."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        dispatch_count: dict[str, int] = {"count": 0}
        lock = threading.Lock()

        def counting_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            with lock:
                dispatch_count["count"] += 1
            return Envelope(
                asap_version="0.1",
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_123",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        registry.register("task.request", counting_handler)

        num_threads = 10
        dispatches_per_thread = 50
        errors: list[Exception] = []

        def dispatch_requests() -> None:
            try:
                for _ in range(dispatches_per_thread):
                    registry.dispatch(sample_task_request_envelope, sample_manifest)
            except Exception as e:
                errors.append(e)

        # Run dispatches concurrently
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(dispatch_requests) for _ in range(num_threads)]
            for future in as_completed(futures):
                future.result()

        # Verify no errors occurred
        assert len(errors) == 0

        # Verify all dispatches completed
        expected_count = num_threads * dispatches_per_thread
        assert dispatch_count["count"] == expected_count

    def test_concurrent_register_and_dispatch(self, sample_manifest: Manifest) -> None:
        """Test mixed concurrent register and dispatch operations."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        results: dict[str, list[bool]] = {"register": [], "dispatch": [], "has_handler": []}
        lock = threading.Lock()

        def simple_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return Envelope(
                asap_version="0.1",
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_123",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        # Pre-register some handlers
        for i in range(5):
            registry.register(f"type.{i}", simple_handler)

        def register_thread() -> None:
            for i in range(100):
                registry.register(f"new.type.{i}", simple_handler)
                with lock:
                    results["register"].append(True)

        def dispatch_thread() -> None:
            envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:client",
                recipient="urn:asap:agent:server",
                payload_type="type.0",
                payload=TaskRequest(
                    conversation_id="conv_1",
                    skill_id="test",
                    input={},
                ).model_dump(),
            )
            for _ in range(100):
                try:
                    registry.dispatch(envelope, sample_manifest)
                    with lock:
                        results["dispatch"].append(True)
                except Exception:
                    with lock:
                        results["dispatch"].append(False)

        def has_handler_thread() -> None:
            for i in range(100):
                result = registry.has_handler(f"type.{i % 5}")
                with lock:
                    results["has_handler"].append(result)

        # Run all operations concurrently
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(register_thread),
                executor.submit(register_thread),
                executor.submit(dispatch_thread),
                executor.submit(dispatch_thread),
                executor.submit(has_handler_thread),
                executor.submit(has_handler_thread),
            ]
            for future in as_completed(futures):
                future.result()

        # Verify operations completed without data corruption
        assert len(results["register"]) == 200
        assert all(results["register"])
        assert len(results["dispatch"]) == 200
        assert all(results["dispatch"])
        assert len(results["has_handler"]) == 200
        assert all(results["has_handler"])

    def test_list_handlers_returns_copy(self) -> None:
        """Test list_handlers returns a copy, not a view."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        registry.register("type.a", handler)
        registry.register("type.b", handler)

        handlers_list = registry.list_handlers()
        original_len = len(handlers_list)

        # Modify the returned list
        handlers_list.append("type.c")

        # Registry should be unchanged
        assert len(registry.list_handlers()) == original_len


class TestDispatchAsync:
    """Tests for async dispatch functionality."""

    @pytest.fixture
    def sample_manifest(self) -> Manifest:
        """Create a sample manifest for testing."""
        return Manifest(
            id="urn:asap:agent:test-async",
            name="Test Async Agent",
            version="1.0.0",
            description="Agent for testing async dispatch",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="async", description="Async skill")],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="http://localhost:8000/asap"),
        )

    @pytest.fixture
    def sample_envelope(self) -> Envelope:
        """Create a sample envelope for testing."""
        return Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-async",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv_async",
                skill_id="async",
                input={"test": "data"},
            ).model_dump(),
        )

    @pytest.mark.asyncio
    async def test_dispatch_async_with_sync_handler(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async correctly handles synchronous handlers."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        handler_called = {"count": 0}

        def sync_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            handler_called["count"] += 1
            return Envelope(
                asap_version="0.1",
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_sync",
                    status=TaskStatus.COMPLETED,
                    result={"sync": True},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        registry.register("task.request", sync_handler)

        response = await registry.dispatch_async(sample_envelope, sample_manifest)

        assert handler_called["count"] == 1
        assert response.payload_type == "task.response"
        assert response.payload_dict["result"]["sync"] is True

    @pytest.mark.asyncio
    async def test_dispatch_async_with_async_handler(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async correctly handles asynchronous handlers."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        handler_called = {"count": 0}

        async def async_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            handler_called["count"] += 1
            return Envelope(
                asap_version="0.1",
                sender=manifest.id,
                recipient=envelope.sender,
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_async",
                    status=TaskStatus.COMPLETED,
                    result={"async": True},
                ).model_dump(),
                correlation_id=envelope.id,
            )

        registry.register("task.request", async_handler)

        response = await registry.dispatch_async(sample_envelope, sample_manifest)

        assert handler_called["count"] == 1
        assert response.payload_type == "task.response"
        assert response.payload_dict["result"]["async"] is True

    @pytest.mark.asyncio
    async def test_dispatch_async_detects_coroutine_function(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test that dispatch_async correctly detects async vs sync handlers."""
        import inspect

        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def sync_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        async def async_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            return envelope

        # Verify detection works
        assert not inspect.iscoroutinefunction(sync_handler)
        assert inspect.iscoroutinefunction(async_handler)

        # Both should work with dispatch_async
        registry.register("sync.type", sync_handler)
        registry.register("async.type", async_handler)

        sync_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="sync.type",
            payload={},
        )
        async_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:server",
            payload_type="async.type",
            payload={},
        )

        # Both should complete without error
        await registry.dispatch_async(sync_envelope, sample_manifest)
        await registry.dispatch_async(async_envelope, sample_manifest)

    @pytest.mark.asyncio
    async def test_dispatch_async_raises_for_unknown_type(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async raises HandlerNotFoundError for unknown types."""
        from asap.transport.handlers import HandlerNotFoundError, HandlerRegistry

        registry = HandlerRegistry()  # Empty registry

        with pytest.raises(HandlerNotFoundError) as exc_info:
            await registry.dispatch_async(sample_envelope, sample_manifest)

        assert exc_info.value.payload_type == "task.request"

    @pytest.mark.asyncio
    async def test_dispatch_async_propagates_handler_exceptions(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async propagates exceptions from handlers."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        def failing_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            raise ValueError("Handler failed intentionally")

        registry.register("task.request", failing_handler)

        with pytest.raises(ValueError, match="Handler failed intentionally"):
            await registry.dispatch_async(sample_envelope, sample_manifest)

    @pytest.mark.asyncio
    async def test_dispatch_async_propagates_async_handler_exceptions(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async propagates exceptions from async handlers."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        async def failing_async_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
            raise RuntimeError("Async handler failed intentionally")

        registry.register("task.request", failing_async_handler)

        with pytest.raises(RuntimeError, match="Async handler failed intentionally"):
            await registry.dispatch_async(sample_envelope, sample_manifest)

    @pytest.mark.asyncio
    async def test_dispatch_async_with_lambda_handler(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async works with lambda handlers."""
        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()

        # Lambda is a sync callable
        registry.register("task.request", lambda e, m: e)

        response = await registry.dispatch_async(sample_envelope, sample_manifest)

        assert response.id == sample_envelope.id

    @pytest.mark.asyncio
    async def test_dispatch_async_with_async_callable_object(
        self, sample_manifest: Manifest, sample_envelope: Envelope
    ) -> None:
        """Test dispatch_async correctly handles async callable objects (classes with async __call__)."""
        import inspect

        from asap.transport.handlers import HandlerRegistry

        registry = HandlerRegistry()
        handler_called = {"count": 0}

        class AsyncCallableHandler:
            """Handler implemented as async callable object."""

            async def __call__(self, envelope: Envelope, manifest: Manifest) -> Envelope:
                """Process envelope asynchronously."""
                handler_called["count"] += 1
                return Envelope(
                    asap_version="0.1",
                    sender=manifest.id,
                    recipient=envelope.sender,
                    payload_type="task.response",
                    payload=TaskResponse(
                        task_id="task_callable",
                        status=TaskStatus.COMPLETED,
                        result={"callable": True},
                    ).model_dump(),
                    correlation_id=envelope.id,
                )

        handler = AsyncCallableHandler()
        # Verify it's not detected as coroutine function but returns awaitable
        assert not inspect.iscoroutinefunction(handler)
        registry.register("task.request", handler)

        response = await registry.dispatch_async(sample_envelope, sample_manifest)

        assert handler_called["count"] == 1
        assert response.payload_type == "task.response"
        assert response.payload_dict["result"]["callable"] is True
