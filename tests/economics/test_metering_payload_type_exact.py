"""Metering records only the exact task.request and task.response pair.

``state.query`` is already rejected. A case, padding, or prefix variant of
``task.request`` or ``task.response`` would still pass that test and bill
traffic that is not a completed task.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from asap.economics.hooks import record_task_usage
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse
from asap.state.metering import InMemoryMeteringStore

_AGENT = "urn:asap:agent:meter-01"
_REQUEST_NEIGHBORS: tuple[str, ...] = (
    "Task.Request",
    " task.request",
    "task.request ",
    "task.requestextra",
    "task.reques",
)
_RESPONSE_NEIGHBORS: tuple[str, ...] = (
    "Task.Response",
    " task.response",
    "task.response ",
    "task.responseextra",
    "task.respons",
)


def _manifest() -> Manifest:
    """Agent manifest whose id is the billed agent."""
    return Manifest(
        id=_AGENT,
        name="Meter Agent",
        version="1.0.0",
        description="Metering payload-type fence",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _request(payload_type: str) -> Envelope:
    """Request envelope whose payload type is the string under test."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:consumer",
        recipient=_AGENT,
        payload_type=payload_type,
        payload=TaskRequest(
            conversation_id="conv_01",
            skill_id="echo",
            input={"text": "hello"},
        ).model_dump(),
    )


def _response(payload_type: str) -> Envelope:
    """Response envelope whose payload type is the string under test."""
    return Envelope(
        asap_version="0.1",
        sender=_AGENT,
        recipient="urn:asap:agent:consumer",
        payload_type=payload_type,
        correlation_id="req_01",
        payload=TaskResponse(
            task_id="task_01",
            status="completed",
            result={"echoed": True},
        ).model_dump(),
    )


async def _stored_count(store: InMemoryMeteringStore) -> int:
    """Count usage rows for the billed agent across a fixed window."""
    events = await store.query(
        _AGENT,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    return len(events)


@pytest.mark.asyncio
async def test_record_task_usage_payload_type_is_exact() -> None:
    """Neighbors of either payload type write nothing. The exact pair writes one row."""
    manifest = _manifest()
    store = InMemoryMeteringStore()

    for payload_type in _REQUEST_NEIGHBORS:
        await record_task_usage(
            store,
            _request(payload_type),
            _response("task.response"),
            duration_ms=10.0,
            manifest=manifest,
        )
    for payload_type in _RESPONSE_NEIGHBORS:
        await record_task_usage(
            store,
            _request("task.request"),
            _response(payload_type),
            duration_ms=10.0,
            manifest=manifest,
        )
    assert await _stored_count(store) == 0

    await record_task_usage(
        store,
        _request("task.request"),
        _response("task.response"),
        duration_ms=10.0,
        manifest=manifest,
    )
    assert await _stored_count(store) == 1
