"""Tests for metering hooks (task lifecycle integration)."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.economics.hooks import record_task_usage, wrap_handler_with_metering, _safe_int
from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse
from asap.state.metering import InMemoryMeteringStore
from asap.models.entities import Capability, Endpoint, Skill
from asap.transport.handlers import HandlerRegistry, create_echo_handler


@pytest.fixture
def metering_store() -> InMemoryMeteringStore:
    """Fresh InMemoryMeteringStore for each test."""
    return InMemoryMeteringStore()


@pytest.fixture
def sample_manifest() -> Manifest:
    """Sample manifest for agent_id."""
    return Manifest(
        id="urn:asap:agent:test-agent",
        name="Test Agent",
        version="1.0.0",
        description="Test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


@pytest.fixture
def task_request_envelope(sample_manifest: Manifest) -> Envelope:
    """Envelope with task.request payload."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:consumer",
        recipient=sample_manifest.id,
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv_01",
            skill_id="echo",
            input={"test": "data"},
        ).model_dump(),
    )


@pytest.fixture
def task_response_envelope(sample_manifest: Manifest) -> Envelope:
    """Envelope with task.response payload (no metrics)."""
    return Envelope(
        asap_version="0.1",
        sender=sample_manifest.id,
        recipient="urn:asap:agent:consumer",
        payload_type="task.response",
        payload=TaskResponse(
            task_id="task_123",
            status="completed",
            result={"echoed": {"test": "data"}},
        ).model_dump(),
        correlation_id="req_01",
    )


class TestRecordTaskUsage:
    """Test record_task_usage extraction and recording."""

    @pytest.mark.asyncio
    async def test_records_usage_with_measured_duration(
        self,
        metering_store: InMemoryMeteringStore,
        task_request_envelope: Envelope,
        task_response_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """record_task_usage stores event with duration, agent, consumer."""
        await record_task_usage(
            metering_store,
            task_request_envelope,
            task_response_envelope,
            duration_ms=1234.5,
            manifest=sample_manifest,
        )

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 1
        assert events[0].task_id == "task_123"
        assert events[0].agent_id == sample_manifest.id
        assert events[0].consumer_id == "urn:asap:agent:consumer"
        assert events[0].metrics.duration_ms == 1234  # int(round(1234.5))
        assert events[0].metrics.tokens_in == 0
        assert events[0].metrics.tokens_out == 0
        assert events[0].metrics.api_calls == 0

    @pytest.mark.asyncio
    async def test_extracts_agent_reported_metrics(
        self,
        metering_store: InMemoryMeteringStore,
        task_request_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """record_task_usage extracts tokens_in, tokens_out, api_calls from TaskResponse.metrics."""
        response_with_metrics = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload=TaskResponse(
                task_id="task_456",
                status="completed",
                result={},
                metrics={"tokens_in": 1500, "tokens_out": 2300, "api_calls": 5},
            ).model_dump(),
            correlation_id="req_02",
        )

        await record_task_usage(
            metering_store,
            task_request_envelope,
            response_with_metrics,
            duration_ms=500.0,
            manifest=sample_manifest,
        )

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 1
        assert events[0].metrics.tokens_in == 1500
        assert events[0].metrics.tokens_out == 2300
        assert events[0].metrics.api_calls == 5
        assert events[0].metrics.duration_ms == 500

    @pytest.mark.asyncio
    async def test_skips_non_task_request(
        self,
        metering_store: InMemoryMeteringStore,
        task_response_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """record_task_usage skips when envelope is not task.request."""
        other_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:other",
            recipient=sample_manifest.id,
            payload_type="state.query",
            payload={"task_id": "t1"},
        )
        await record_task_usage(
            metering_store,
            other_envelope,
            task_response_envelope,
            duration_ms=100.0,
            manifest=sample_manifest,
        )
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_skips_when_response_not_task_response(
        self,
        metering_store: InMemoryMeteringStore,
        task_request_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """record_task_usage skips when response is not task.response."""
        other_response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.update",
            payload={
                "task_id": "t1",
                "update_type": "progress",
                "status": "working",
            },
        )
        await record_task_usage(
            metering_store,
            task_request_envelope,
            other_response,
            duration_ms=100.0,
            manifest=sample_manifest,
        )
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 0


class TestHandlerRegistryMetering:
    """Test HandlerRegistry integration with metering_store."""

    @pytest.mark.asyncio
    async def test_registry_records_on_task_completion(
        self,
        metering_store: InMemoryMeteringStore,
        task_request_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """HandlerRegistry with metering_store records usage after task.request."""
        registry = HandlerRegistry(metering_store=metering_store)
        registry.register("task.request", create_echo_handler())

        response = await registry.dispatch_async(task_request_envelope, sample_manifest)

        assert response.payload_type == "task.response"
        task_id = response.payload_dict.get("task_id")
        assert task_id is not None

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 1
        assert events[0].task_id == task_id
        assert events[0].agent_id == sample_manifest.id
        assert events[0].consumer_id == "urn:asap:agent:consumer"
        assert events[0].metrics.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_registry_no_double_counting(
        self,
        metering_store: InMemoryMeteringStore,
        task_request_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """Single dispatch records exactly one event (no double-counting)."""
        registry = HandlerRegistry(metering_store=metering_store)
        registry.register("task.request", create_echo_handler())

        await registry.dispatch_async(task_request_envelope, sample_manifest)

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = await metering_store.query(sample_manifest.id, start, end)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_registry_without_metering_store_does_not_record(
        self,
        task_request_envelope: Envelope,
        sample_manifest: Manifest,
    ) -> None:
        """HandlerRegistry without metering_store does not fail (no recording)."""
        registry = HandlerRegistry(metering_store=None)
        registry.register("task.request", create_echo_handler())

        response = await registry.dispatch_async(task_request_envelope, sample_manifest)
        assert response.payload_type == "task.response"


class TestCreateAppMeteringIntegration:
    """Test create_app with metering_store via HTTP."""

    def test_create_app_with_metering_store_records_on_post_asap(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
        isolated_rate_limiter: object,
    ) -> None:
        """POST /asap with metering_store records usage to store."""
        from asap.transport.server import create_app

        app: FastAPI = create_app(
            sample_manifest,
            metering_store=metering_store,
            rate_limit="999999/minute",
        )
        if isolated_rate_limiter is not None:
            app.state.limiter = isolated_rate_limiter

        client = TestClient(app)
        rpc_body = {
            "jsonrpc": "2.0",
            "method": "asap.send",
            "params": {
                "envelope": {
                    "asap_version": "0.1",
                    "sender": "urn:asap:agent:consumer",
                    "recipient": sample_manifest.id,
                    "payload_type": "task.request",
                    "payload": {
                        "conversation_id": "conv_01",
                        "skill_id": "echo",
                        "input": {"test": "data"},
                    },
                }
            },
            "id": "req-1",
        }

        response = client.post("/asap", json=rpc_body)
        assert response.status_code == 200

        data = response.json()
        assert "result" in data
        assert "envelope" in data["result"]

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        events = asyncio.run(metering_store.query(sample_manifest.id, start, end))
        assert len(events) == 1
        assert events[0].agent_id == sample_manifest.id
        assert events[0].consumer_id == "urn:asap:agent:consumer"


class TestWrapHandlerWithMetering:
    """Tests for wrap_handler_with_metering decorator."""

    @pytest.mark.asyncio
    async def test_wraps_async_handler(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        async def my_handler(env: Envelope, mf: Manifest) -> Envelope:
            await asyncio.sleep(0.01)
            # Must return a task.response for metering to record it
            return Envelope(
                asap_version="0.1",
                sender=mf.id,
                recipient=env.sender,
                payload_type="task.response",
                payload={"task_id": "t1", "status": "completed", "result": {}},
                correlation_id=env.id,
            )

        wrapped = wrap_handler_with_metering(my_handler, metering_store, sample_manifest)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={
                "conversation_id": "c1",
                "skill_id": "s",
                "input": {},
            },
        )

        result = await wrapped(envelope, sample_manifest)
        assert result.payload_type == "task.response"

        # Check store
        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) > 0  # Should record event
        assert events[0].metrics.duration_ms >= 10

    @pytest.mark.asyncio
    async def test_wraps_sync_handler(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        def my_sync_handler(env: Envelope, mf: Manifest) -> Envelope:
            import time

            time.sleep(0.01)
            return Envelope(
                asap_version="0.1",
                sender=mf.id,
                recipient=env.sender,
                payload_type="task.response",
                payload={"task_id": "t1", "status": "completed", "result": {}},
                correlation_id=env.id,
            )

        wrapped = wrap_handler_with_metering(my_sync_handler, metering_store, sample_manifest)

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={
                "conversation_id": "c1",
                "skill_id": "s",
                "input": {},
            },
        )

        # The wrapper returns a coroutine even if handler is sync
        result = await wrapped(envelope, sample_manifest)
        assert result.payload_type == "task.response"

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) > 0
        assert events[0].metrics.duration_ms >= 10

    @pytest.mark.asyncio
    async def test_returns_original_handler_if_store_none(self) -> None:
        async def my_handler(env: Envelope, mf: Manifest) -> Envelope:
            return env

        # Pass None as store
        wrapped = wrap_handler_with_metering(my_handler, None, None)
        assert wrapped is my_handler


class TestSafeInt:
    """Tests for internal _safe_int helper."""

    def test_safe_int_values(self) -> None:
        assert _safe_int(None) == 0
        assert _safe_int(None, 42) == 42
        assert _safe_int(10) == 10
        assert _safe_int(-5) == 0
        assert _safe_int("10") == 10
        assert _safe_int("10.5") == 0  # int("10.5") raises ValueError
        assert _safe_int("abc", 5) == 5
        assert _safe_int([], 7) == 7


class TestRecordTaskUsageEdgeCases:
    """Tests for record_task_usage edge cases."""

    @pytest.mark.asyncio
    async def test_safe_int_parsing_in_context(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={"conversation_id": "c", "skill_id": "s", "input": {}},
        )
        response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload={
                "task_id": "t1",
                "status": "completed",
                "result": {},
                "metrics": {
                    "tokens_in": 100,
                    "tokens_out": 200,
                    "api_calls": 5,
                },
            },
            correlation_id="req-01",
        )

        await record_task_usage(metering_store, envelope, response, 100, sample_manifest)

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) == 1
        m = events[0].metrics
        assert m.tokens_in == 100
        assert m.tokens_out == 200
        assert m.api_calls == 5

    @pytest.mark.asyncio
    async def test_record_task_usage_with_valid_response(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        """record_task_usage records event when request and response are valid."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={"conversation_id": "c", "skill_id": "s", "input": {}},
        )
        response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload={
                "task_id": "t1",
                "status": "completed",
                "result": {},
            },
            correlation_id="req-01",
        )

        await record_task_usage(metering_store, envelope, response, 100, sample_manifest)

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) == 1
        assert events[0].task_id == "t1"

    @pytest.mark.asyncio
    async def test_tokens_used_fallback(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={"conversation_id": "c", "skill_id": "s", "input": {}},
        )
        response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload={
                "task_id": "t1",
                "status": "completed",
                "result": {},
                "metrics": {
                    "tokens_in": 10,
                    "tokens_out": 0,  # Explicit 0
                    "tokens_used": 55,  # Should be used as fallback
                },
            },
            correlation_id="req-01",
        )

        await record_task_usage(metering_store, envelope, response, 100, sample_manifest)

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) == 1
        assert events[0].metrics.tokens_out == 55

    @pytest.mark.asyncio
    async def test_negative_duration_clamped(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={"conversation_id": "c", "skill_id": "s", "input": {}},
        )
        response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload={"task_id": "t1", "status": "completed"},
            correlation_id="req-01",
        )

        await record_task_usage(metering_store, envelope, response, -50.0, sample_manifest)

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(events) == 1
        assert events[0].metrics.duration_ms == 0

    @pytest.mark.asyncio
    async def test_skips_when_task_id_empty(
        self,
        metering_store: InMemoryMeteringStore,
        sample_manifest: Manifest,
    ) -> None:
        """An empty task_id must not create a metering row."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:consumer",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload={"conversation_id": "c", "skill_id": "s", "input": {}},
        )
        response = Envelope(
            asap_version="0.1",
            sender=sample_manifest.id,
            recipient="urn:asap:agent:consumer",
            payload_type="task.response",
            payload={"task_id": "", "status": "completed"},
            correlation_id="req-01",
        )

        await record_task_usage(metering_store, envelope, response, 100, sample_manifest)

        events = await metering_store.query(
            sample_manifest.id,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert events == []
