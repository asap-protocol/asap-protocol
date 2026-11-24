"""Benchmarks for ASAP model serialization and validation.

These benchmarks measure the performance of:
- Envelope creation with auto-generated fields
- Model serialization to JSON
- Model deserialization from JSON
- Schema validation

Performance targets:
- Envelope creation: < 100μs
- JSON serialization: < 50μs
- JSON deserialization: < 100μs
- Validation: < 200μs

Run with: uv run pytest benchmarks/benchmark_models.py --benchmark-only -v
"""

from datetime import datetime, timezone
from typing import Any


from asap.models.entities import (
    Agent,
    Artifact,
    Conversation,
    Manifest,
    Message,
    Task,
)
from asap.models.envelope import Envelope
from asap.models.enums import MessageRole, TaskStatus
from asap.models.payloads import TaskRequest, TaskResponse


class TestEnvelopeCreation:
    """Benchmarks for envelope creation performance."""

    def test_envelope_creation_minimal(self, benchmark: Any) -> None:
        """Benchmark minimal envelope creation with auto-generated fields."""

        def create_envelope() -> Envelope:
            return Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:sender",
                recipient="urn:asap:agent:recipient",
                payload_type="task.request",
                payload={"conversation_id": "conv_001", "skill_id": "echo", "input": {}},
            )

        result = benchmark(create_envelope)
        assert result.id is not None
        assert result.timestamp is not None

    def test_envelope_creation_full(self, benchmark: Any) -> None:
        """Benchmark full envelope creation with all fields."""

        def create_envelope() -> Envelope:
            return Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:sender",
                recipient="urn:asap:agent:recipient",
                payload_type="task.response",
                payload={
                    "task_id": "01JGQXYZ1234567890123456",
                    "status": "completed",
                    "result": {"message": "success", "data": [1, 2, 3, 4, 5]},
                },
                correlation_id="corr_001",
                trace_id="trace_001",
                extensions={"custom": "value"},
            )

        result = benchmark(create_envelope)
        assert result.correlation_id == "corr_001"


class TestJsonSerialization:
    """Benchmarks for JSON serialization performance."""

    def test_envelope_to_json(self, benchmark: Any, sample_envelope: Envelope) -> None:
        """Benchmark envelope serialization to JSON dict."""
        result = benchmark(sample_envelope.model_dump, mode="json")
        assert "asap_version" in result

    def test_envelope_to_json_string(self, benchmark: Any, sample_envelope: Envelope) -> None:
        """Benchmark envelope serialization to JSON string."""
        result = benchmark(sample_envelope.model_dump_json)
        assert "asap_version" in result

    def test_manifest_to_json(self, benchmark: Any, sample_manifest: Manifest) -> None:
        """Benchmark manifest serialization to JSON."""
        result = benchmark(sample_manifest.model_dump, mode="json")
        assert "capabilities" in result

    def test_complex_payload_serialization(self, benchmark: Any) -> None:
        """Benchmark complex payload with nested data."""
        response = TaskResponse(
            task_id="01JGQXYZ1234567890123456",
            status=TaskStatus.COMPLETED,
            result={
                "summary": "Test result with complex nested data",
                "items": [{"id": i, "value": f"item_{i}"} for i in range(10)],
            },
            metrics={
                "processed_at": "2024-01-15T10:30:00Z",
                "duration_ms": 150,
                "tags": ["benchmark", "test", "performance"],
            },
        )

        result = benchmark(response.model_dump, mode="json")
        assert result["status"] == "completed"


class TestJsonDeserialization:
    """Benchmarks for JSON deserialization performance."""

    def test_envelope_from_json(self, benchmark: Any) -> None:
        """Benchmark envelope deserialization from JSON dict."""
        data = {
            "id": "01JGQXYZ1234567890123456",
            "asap_version": "0.1",
            "timestamp": "2024-01-15T10:30:00Z",
            "sender": "urn:asap:agent:sender",
            "recipient": "urn:asap:agent:recipient",
            "payload_type": "task.request",
            "payload": {
                "conversation_id": "01JGQXYZ1234567890123456",
                "skill_id": "echo",
                "input": {},
            },
        }

        result = benchmark(Envelope.model_validate, data)
        assert result.asap_version == "0.1"

    def test_manifest_from_json(self, benchmark: Any) -> None:
        """Benchmark manifest deserialization from JSON dict."""
        data = {
            "id": "urn:asap:agent:test",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "Test agent for benchmarks",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "skill1", "description": "Test skill"}],
                "state_persistence": False,
                "streaming": False,
                "mcp_tools": [],
            },
            "endpoints": {"asap": "http://localhost:8000/asap"},
        }

        result = benchmark(Manifest.model_validate, data)
        assert result.name == "Test Agent"

    def test_task_request_from_json(self, benchmark: Any) -> None:
        """Benchmark task request deserialization."""
        data = {
            "conversation_id": "01JGQXYZ1234567890123456",
            "skill_id": "research",
            "input": {
                "query": "ASAP protocol performance",
                "max_results": 10,
                "filters": {"date_range": "last_month"},
            },
            "config": {"priority": "high", "timeout_seconds": 30},
        }

        result = benchmark(TaskRequest.model_validate, data)
        assert result.skill_id == "research"


class TestEntityCreation:
    """Benchmarks for entity creation performance."""

    def test_agent_creation(self, benchmark: Any) -> None:
        """Benchmark agent creation."""

        def create_agent() -> Agent:
            return Agent(
                id="urn:asap:agent:benchmark",
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute", "state.persist"],
            )

        result = benchmark(create_agent)
        assert result.id == "urn:asap:agent:benchmark"

    def test_task_creation(self, benchmark: Any) -> None:
        """Benchmark task creation."""
        now = datetime.now(timezone.utc)

        def create_task() -> Task:
            return Task(
                id="01JGQXYZ1234567890123456",
                conversation_id="01JGQXYZ1234567890123457",
                status=TaskStatus.SUBMITTED,
                created_at=now,
                updated_at=now,
            )

        result = benchmark(create_task)
        assert result.status == TaskStatus.SUBMITTED

    def test_message_creation(self, benchmark: Any) -> None:
        """Benchmark message creation with parts."""
        now = datetime.now(timezone.utc)

        def create_message() -> Message:
            return Message(
                id="01JGQXYZ1234567890123456",
                task_id="01JGQXYZ1234567890123457",
                sender="urn:asap:agent:coordinator",
                role=MessageRole.ASSISTANT,
                parts=["01JGQXYZ1234567890123458", "01JGQXYZ1234567890123459"],
                timestamp=now,
            )

        result = benchmark(create_message)
        assert len(result.parts) == 2

    def test_conversation_creation(self, benchmark: Any) -> None:
        """Benchmark conversation creation."""
        now = datetime.now(timezone.utc)

        def create_conversation() -> Conversation:
            return Conversation(
                id="01JGQXYZ1234567890123456",
                participants=["urn:asap:agent:client", "urn:asap:agent:server"],
                created_at=now,
            )

        result = benchmark(create_conversation)
        assert "urn:asap:agent:client" in result.participants

    def test_artifact_creation(self, benchmark: Any) -> None:
        """Benchmark artifact creation."""
        now = datetime.now(timezone.utc)

        def create_artifact() -> Artifact:
            return Artifact(
                id="01JGQXYZ1234567890123456",
                task_id="01JGQXYZ1234567890123457",
                name="report.pdf",
                parts=["01JGQXYZ1234567890123458"],
                created_at=now,
            )

        result = benchmark(create_artifact)
        assert result.name == "report.pdf"


class TestBatchOperations:
    """Benchmarks for batch operations."""

    def test_create_100_envelopes(self, benchmark: Any) -> None:
        """Benchmark creating 100 envelopes in sequence."""

        def create_batch() -> list[Envelope]:
            return [
                Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:sender",
                    recipient="urn:asap:agent:recipient",
                    payload_type="task.request",
                    payload={"conversation_id": f"conv_{i}", "skill_id": "echo", "input": {}},
                )
                for i in range(100)
            ]

        result = benchmark(create_batch)
        assert len(result) == 100

    def test_serialize_100_envelopes(self, benchmark: Any) -> None:
        """Benchmark serializing 100 envelopes."""
        envelopes = [
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:sender",
                recipient="urn:asap:agent:recipient",
                payload_type="task.request",
                payload={"conversation_id": f"conv_{i}", "skill_id": "echo", "input": {}},
            )
            for i in range(100)
        ]

        def serialize_batch() -> list[dict[str, Any]]:
            return [env.model_dump(mode="json") for env in envelopes]

        result = benchmark(serialize_batch)
        assert len(result) == 100
