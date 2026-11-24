"""Tests for ASAP protocol error handling."""

from asap.errors import (
    ASAPError,
    RPC_CONNECTION_ERROR,
    InvalidTransitionError,
    MalformedEnvelopeError,
    RPC_REMOTE_GENERIC,
    RecoverableError,
    RemoteRPCError,
    TaskNotFoundError,
    TaskAlreadyCompletedError,
    ThreadPoolExhaustedError,
    ASAPConnectionError,
    is_asap_json_rpc_code,
    jsonrpc_error_data_for_asap_exception,
    remote_rpc_error_from_json,
)


class TestASAPError:
    """Test ASAPError base class."""

    def test_basic_error_creation(self) -> None:
        error = ASAPError(code="asap:test/error", message="Test error message")

        assert error.code == "asap:test/error"
        assert error.message == "Test error message"
        assert error.details == {}
        assert str(error) == "Test error message"

    def test_error_with_details(self) -> None:
        details = {"context": "test", "value": 42}
        error = ASAPError(
            code="asap:test/detailed_error", message="Detailed error", details=details
        )

        assert error.code == "asap:test/detailed_error"
        assert error.message == "Detailed error"
        assert error.details == details

    def test_error_details_immutability(self) -> None:
        error1 = ASAPError("code", "msg", {"key": "value1"})
        error2 = ASAPError("code", "msg", {"key": "value2"})

        assert error1.details["key"] == "value1"
        assert error2.details["key"] == "value2"


class TestInvalidTransitionError:
    """Test InvalidTransitionError class."""

    def test_basic_transition_error(self) -> None:
        error = InvalidTransitionError(from_state="working", to_state="completed")

        assert error.code == "asap:protocol/invalid_state"
        assert error.from_state == "working"
        assert error.to_state == "completed"
        assert "Invalid transition from 'working' to 'completed'" in str(error)

    def test_transition_error_with_details(self) -> None:
        details = {"task_id": "task-123", "reason": "business_logic"}
        error = InvalidTransitionError(from_state="completed", to_state="working", details=details)

        assert error.code == "asap:protocol/invalid_state"
        assert error.from_state == "completed"
        assert error.to_state == "working"
        assert error.details["task_id"] == "task-123"
        assert error.details["reason"] == "business_logic"
        assert error.details["from_state"] == "completed"
        assert error.details["to_state"] == "working"

    def test_transition_error_inheritance(self) -> None:
        error = InvalidTransitionError("state1", "state2")

        assert isinstance(error, ASAPError)
        assert isinstance(error, Exception)


class TestMalformedEnvelopeError:
    """Test MalformedEnvelopeError class."""

    def test_basic_malformed_envelope_error(self) -> None:
        error = MalformedEnvelopeError(reason="missing required field 'id'")

        assert error.code == "asap:protocol/malformed_envelope"
        assert error.reason == "missing required field 'id'"
        assert "Malformed envelope: missing required field 'id'" in str(error)

    def test_malformed_envelope_error_with_details(self) -> None:
        details = {"field": "payload", "expected_type": "dict"}
        error = MalformedEnvelopeError(reason="invalid payload type", details=details)

        assert error.code == "asap:protocol/malformed_envelope"
        assert error.details == details

    def test_malformed_envelope_error_inheritance(self) -> None:
        error = MalformedEnvelopeError("test reason")

        assert isinstance(error, ASAPError)
        assert isinstance(error, Exception)


class TestTaskNotFoundError:
    """Test TaskNotFoundError class."""

    def test_basic_task_not_found_error(self) -> None:
        error = TaskNotFoundError(task_id="task-123")

        assert error.code == "asap:task/not_found"
        assert error.task_id == "task-123"
        assert "Task not found: task-123" in str(error)

    def test_task_not_found_error_with_details(self) -> None:
        details = {"searched_in": "database", "timestamp": "2026-01-19T10:00:00Z"}
        error = TaskNotFoundError(task_id="task-456", details=details)

        assert error.code == "asap:task/not_found"
        assert error.task_id == "task-456"
        assert error.details["searched_in"] == "database"
        assert error.details["task_id"] == "task-456"  # Should be included in details

    def test_task_not_found_error_inheritance(self) -> None:
        error = TaskNotFoundError("task-789")

        assert isinstance(error, ASAPError)
        assert isinstance(error, Exception)


class TestTaskAlreadyCompletedError:
    """Test TaskAlreadyCompletedError class."""

    def test_basic_task_already_completed_error(self) -> None:
        error = TaskAlreadyCompletedError(task_id="task-123", current_status="completed")

        assert error.code == "asap:task/already_completed"
        assert error.task_id == "task-123"
        assert error.current_status == "completed"
        assert "Task already completed: task-123 (status: completed)" in str(error)

    def test_task_already_completed_error_with_details(self) -> None:
        details = {"completed_at": "2026-01-19T09:30:00Z", "output_size": 1024}
        error = TaskAlreadyCompletedError(
            task_id="task-456", current_status="failed", details=details
        )

        assert error.code == "asap:task/already_completed"
        assert error.task_id == "task-456"
        assert error.current_status == "failed"
        assert error.details["completed_at"] == "2026-01-19T09:30:00Z"
        assert error.details["task_id"] == "task-456"  # Should be included in details
        assert error.details["current_status"] == "failed"  # Should be included in details

    def test_task_already_completed_error_inheritance(self) -> None:
        error = TaskAlreadyCompletedError("task-789", "cancelled")

        assert isinstance(error, ASAPError)
        assert isinstance(error, Exception)


class TestRecoverableTransportErrors:
    """ASAP client transport errors participate in the recoverable taxonomy."""

    def test_connection_error_is_recoverable(self) -> None:
        err = ASAPConnectionError("down", url="http://localhost:1")
        assert isinstance(err, RecoverableError)


class TestErrorSerialization:
    """Test error serialization to dictionary."""

    def test_asap_error_to_dict_basic(self) -> None:
        error = ASAPError(code="asap:test/error", message="Test error message")
        result = error.to_dict()

        assert result == {
            "code": "asap:test/error",
            "rpc_code": RPC_REMOTE_GENERIC,
            "message": "Test error message",
            "details": {},
        }

    def test_asap_error_to_dict_with_details(self) -> None:
        """Test ASAPError serialization with details."""
        details = {"context": "test", "value": 42, "nested": {"key": "value"}}
        error = ASAPError(
            code="asap:test/detailed_error", message="Detailed error", details=details
        )
        result = error.to_dict()

        assert result == {
            "code": "asap:test/detailed_error",
            "rpc_code": RPC_REMOTE_GENERIC,
            "message": "Detailed error",
            "details": details,
        }

    def test_invalid_transition_error_to_dict(self) -> None:
        """Test InvalidTransitionError serialization."""
        error = InvalidTransitionError(from_state="working", to_state="completed")
        result = error.to_dict()

        assert result["code"] == "asap:protocol/invalid_state"
        assert "Invalid transition from 'working' to 'completed'" in result["message"]
        assert result["details"]["from_state"] == "working"
        assert result["details"]["to_state"] == "completed"

    def test_malformed_envelope_error_to_dict(self) -> None:
        """Test MalformedEnvelopeError serialization."""
        error = MalformedEnvelopeError(reason="missing required field 'id'")
        result = error.to_dict()

        assert result["code"] == "asap:protocol/malformed_envelope"
        assert "Malformed envelope: missing required field 'id'" in result["message"]
        assert result["details"] == {}

    def test_task_not_found_error_to_dict(self) -> None:
        """Test TaskNotFoundError serialization."""
        error = TaskNotFoundError(task_id="task-123")
        result = error.to_dict()

        assert result["code"] == "asap:task/not_found"
        assert "Task not found: task-123" in result["message"]
        assert result["details"]["task_id"] == "task-123"

    def test_task_already_completed_error_to_dict(self) -> None:
        """Test TaskAlreadyCompletedError serialization."""
        error = TaskAlreadyCompletedError(task_id="task-123", current_status="completed")
        result = error.to_dict()

        assert result["code"] == "asap:task/already_completed"
        assert "Task already completed: task-123 (status: completed)" in result["message"]
        assert result["details"]["task_id"] == "task-123"
        assert result["details"]["current_status"] == "completed"

    def test_error_to_dict_is_json_serializable(self) -> None:
        """Test that to_dict() returns JSON-serializable data."""
        import json

        error = ASAPError(
            code="asap:test/error",
            message="Test error",
            details={"number": 42, "string": "value", "list": [1, 2, 3]},
        )
        result = error.to_dict()

        # Should not raise exception
        json_str = json.dumps(result)
        assert isinstance(json_str, str)

        # Should be able to parse back
        parsed = json.loads(json_str)
        assert parsed == result


class TestThreadPoolExhaustedError:
    """Test ThreadPoolExhaustedError class."""

    def test_basic_thread_pool_exhausted_error(self) -> None:
        """Test creating a ThreadPoolExhaustedError."""
        error = ThreadPoolExhaustedError(max_threads=10, active_threads=10)

        assert error.code == "asap:transport/thread_pool_exhausted"
        assert error.max_threads == 10
        assert error.active_threads == 10
        assert "Thread pool exhausted: 10/10 threads in use" in str(error)
        assert "Service temporarily unavailable" in str(error)

    def test_thread_pool_exhausted_error_with_details(self) -> None:
        """Test ThreadPoolExhaustedError with additional details."""
        details = {"queue_size": 100, "rejected_requests": 5}
        error = ThreadPoolExhaustedError(max_threads=20, active_threads=18, details=details)

        assert error.code == "asap:transport/thread_pool_exhausted"
        assert error.max_threads == 20
        assert error.active_threads == 18
        assert error.details["queue_size"] == 100
        assert error.details["rejected_requests"] == 5
        assert error.details["max_threads"] == 20
        assert error.details["active_threads"] == 18

    def test_thread_pool_exhausted_error_inheritance(self) -> None:
        """Test that ThreadPoolExhaustedError inherits from ASAPError."""
        error = ThreadPoolExhaustedError(max_threads=5, active_threads=5)

        assert isinstance(error, ASAPError)
        assert isinstance(error, Exception)

    def test_thread_pool_exhausted_error_to_dict(self) -> None:
        """Test ThreadPoolExhaustedError serialization."""
        from asap.errors import RPC_THREAD_POOL_EXHAUSTED

        error = ThreadPoolExhaustedError(max_threads=15, active_threads=15)
        result = error.to_dict()

        assert result["code"] == "asap:transport/thread_pool_exhausted"
        assert result["rpc_code"] == RPC_THREAD_POOL_EXHAUSTED
        assert "Thread pool exhausted" in result["message"]
        assert result["details"]["max_threads"] == 15
        assert result["details"]["active_threads"] == 15
        assert result.get("retry_after_ms") is not None


class TestJsonRpcErrorInterop:
    """Tests for JSON-RPC interop and metadata shaping."""

    def test_asap_json_rpc_code_boundaries(self) -> None:
        """ASAP JSON-RPC helper accepts only reserved code range."""
        assert is_asap_json_rpc_code(-32059) is True
        assert is_asap_json_rpc_code(-32000) is True
        assert is_asap_json_rpc_code(-31999) is False
        assert is_asap_json_rpc_code(-32060) is False

    def test_asap_error_rejects_out_of_range_rpc_code(self) -> None:
        """Base ASAPError rejects JSON-RPC codes outside ASAP reserved slot."""
        import pytest

        with pytest.raises(ValueError, match="rpc_code must be in"):
            ASAPError(code="asap:test/error", message="bad rpc", rpc_code=-32603)

    def test_remote_rpc_error_from_json_returns_recoverable_and_strips_meta(self) -> None:
        """Recoverable remote payload is typed and metadata fields are normalized."""
        err = remote_rpc_error_from_json(
            rpc_code=-32033,
            message="remote timeout",
            data={
                "recoverable": True,
                "retry_after_ms": 250,
                "alternative_agents": ["urn:asap:agent:secondary"],
                "fallback_action": "retry_with_backoff",
                "asap_taxonomy_code": "asap:transport/timeout",
                "rpc_code": -32033,
                "upstream": "worker-1",
            },
        )

        assert isinstance(err, RemoteRPCError)
        assert err.is_recoverable is True
        assert err.rpc_code == -32033
        assert err.json_rpc_code == -32033
        assert err.code == "asap:transport/timeout"
        assert err.retry_after_ms == 250
        assert err.alternative_agents == ["urn:asap:agent:secondary"]
        assert err.fallback_action == "retry_with_backoff"
        assert err.details == {"upstream": "worker-1"}

    def test_remote_rpc_error_from_json_uses_code_field_fallback(self) -> None:
        """A taxonomy-like `code` field in data overrides default taxonomy mapping."""
        err = remote_rpc_error_from_json(
            rpc_code=-32040,
            message="resource exhausted",
            data={
                "recoverable": False,
                "code": "asap:resource/exhausted",
                "queue_depth": 11,
            },
        )

        assert isinstance(err, RemoteRPCError)
        assert err.is_recoverable is False
        assert err.code == "asap:resource/exhausted"
        assert err.details == {"queue_depth": 11}

    def test_remote_rpc_error_preserves_wire_code_but_normalizes_internal_slot(self) -> None:
        """Non-ASAP wire code is preserved while internal rpc_code uses remote-generic slot."""
        err = remote_rpc_error_from_json(
            rpc_code=-32603,
            message="internal error",
            data={"recoverable": False, "foo": "bar"},
        )

        assert isinstance(err, RemoteRPCError)
        assert err.is_recoverable is False
        assert err.json_rpc_code == -32603
        assert err.rpc_code == RPC_REMOTE_GENERIC
        assert err.details == {"foo": "bar"}

    def test_jsonrpc_error_data_for_recoverable_exception(self) -> None:
        """Server-side JSON-RPC data payload includes taxonomy and recoverability hints."""
        err = ASAPConnectionError(
            "connection refused",
            url="https://agent.example.test/asap",
            rpc_code=RPC_CONNECTION_ERROR,
            retry_after_ms=500,
            alternative_agents=["urn:asap:agent:fallback"],
        )

        payload = jsonrpc_error_data_for_asap_exception(err)
        assert payload["code"] == "asap:transport/connection_error"
        assert payload["rpc_code"] == RPC_CONNECTION_ERROR
        assert payload["recoverable"] is True
        assert payload["asap_taxonomy_code"] == "asap:transport/connection_error"
        assert payload["retry_after_ms"] == 500
        assert payload["alternative_agents"] == ["urn:asap:agent:fallback"]

    def test_connection_error_message_not_duplicated_when_verify_text_present(self) -> None:
        """Connection errors with existing troubleshooting guidance are not duplicated."""
        err = ASAPConnectionError(
            "Verify service availability before retrying.",
            url="https://agent.example.test/asap",
        )
        assert str(err) == "Verify service availability before retrying."

    def test_remote_rpc_error_constructor_sets_is_recoverable(self) -> None:
        """One class covers both tiers; ``is_recoverable`` defaults to False."""
        fatal = RemoteRPCError(-32603, "fatal", {"foo": "bar"})
        recoverable = RemoteRPCError(
            -32033, "retryable", {"retry_after_ms": 250}, is_recoverable=True
        )

        assert fatal.is_recoverable is False
        assert recoverable.is_recoverable is True
        assert fatal.json_rpc_code == -32603
        assert recoverable.json_rpc_code == -32033
        assert str(fatal) == "Remote error -32603: fatal"
        assert str(recoverable) == "Remote error -32033: retryable"
