"""Metering must ignore a completed task that has no task id."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from asap.economics.hooks import record_task_usage
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.state.metering import InMemoryMeteringStore


def _manifest() -> Manifest:
    return Manifest(
        id="urn:asap:agent:metering-task-id",
        name="Metering Task Id",
        version="1.0.0",
        description="Test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _request(manifest: Manifest) -> Envelope:
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:consumer",
        recipient=manifest.id,
        payload_type="task.request",
        payload={"conversation_id": "c", "skill_id": "echo", "input": {}},
    )


def _response(manifest: Manifest, task_id: str) -> Envelope:
    return Envelope(
        asap_version="0.1",
        sender=manifest.id,
        recipient="urn:asap:agent:consumer",
        payload_type="task.response",
        payload={"task_id": task_id, "status": "completed", "result": {}},
        correlation_id="req-task-id",
    )


@pytest.mark.asyncio
async def test_record_task_usage_skips_empty_task_id() -> None:
    """A blank task_id writes nothing, and a later real task_id still records."""
    store = InMemoryMeteringStore()
    manifest = _manifest()
    await record_task_usage(
        store,
        _request(manifest),
        _response(manifest, ""),
        12.0,
        manifest,
    )
    await record_task_usage(
        store,
        _request(manifest),
        _response(manifest, "task-real"),
        12.0,
        manifest,
    )

    events = await store.query(
        manifest.id,
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2100, 1, 1, tzinfo=timezone.utc),
    )
    assert [event.task_id for event in events] == ["task-real"]
