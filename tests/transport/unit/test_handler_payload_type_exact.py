"""Handler maps match the registered payload type string, not a substring.

Envelope parsing folds ``Task.Request`` into ``TaskRequest``. Dispatch does
not. This file locks the map itself: a longer or shorter name must not run
the handler registered for ``task.request``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.handlers import Handler, HandlerNotFoundError, HandlerRegistry, StreamingHandler

_REGISTERED = "task.request"
# Longer, shorter, and folded aliases. Folding turns Task.Request into the
# same key as task.request; dispatch still looks up the raw string.
_NEIGHBORS: tuple[str, ...] = (
    "task.request.admin",
    "task.requestextra",
    "task.re",
    "equest",
    "Task.Request",
    "TaskRequest",
    " task.request ",
)


def _envelope(payload_type: str) -> Envelope:
    """Build a task envelope whose payload type is the lookup key."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type=payload_type,
        payload=TaskRequest(
            conversation_id="conv-handler-exact",
            skill_id="echo",
            input={"message": "hello"},
        ).model_dump(),
    )


def _sync_handler(calls: list[str]) -> Handler:
    """Return a sync handler that records each payload type it actually runs."""

    def handler(envelope: Envelope, manifest: Manifest) -> Envelope:
        calls.append(envelope.payload_type)
        return envelope

    return handler


def _stream_handler(calls: list[str]) -> StreamingHandler:
    """Return a streaming handler that records each payload type it yields."""

    async def handler(envelope: Envelope, manifest: Manifest) -> AsyncIterator[Envelope]:
        calls.append(envelope.payload_type)
        yield envelope

    return handler


def test_sync_dispatch_payload_type_match_is_exact(sample_manifest: Manifest) -> None:
    """Only the registered string reaches the sync handler."""
    calls: list[str] = []
    registry = HandlerRegistry()
    registry.register(_REGISTERED, _sync_handler(calls))

    delivered = registry.dispatch(_envelope(_REGISTERED), sample_manifest)

    assert delivered.payload_type == _REGISTERED
    assert calls == [_REGISTERED]
    for payload_type in _NEIGHBORS:
        with pytest.raises(HandlerNotFoundError) as caught:
            registry.dispatch(_envelope(payload_type), sample_manifest)
        assert caught.value.payload_type == payload_type
    assert calls == [_REGISTERED]
    assert registry.has_handler(_REGISTERED) is True
    assert all(registry.has_handler(payload_type) is False for payload_type in _NEIGHBORS)


async def test_stream_dispatch_payload_type_match_is_exact(sample_manifest: Manifest) -> None:
    """Only the registered string reaches the streaming handler."""
    calls: list[str] = []
    registry = HandlerRegistry()
    registry.register_streaming_handler(_REGISTERED, _stream_handler(calls))
    exact = _envelope(_REGISTERED)

    streamed = [item async for item in registry.dispatch_stream_async(exact, sample_manifest)]

    assert streamed == [exact]
    assert calls == [_REGISTERED]
    for payload_type in _NEIGHBORS:
        with pytest.raises(HandlerNotFoundError) as caught:
            async for _item in registry.dispatch_stream_async(
                _envelope(payload_type), sample_manifest
            ):
                pass
        assert caught.value.payload_type == payload_type
    assert calls == [_REGISTERED]
    assert registry.has_streaming_handler(_REGISTERED) is True
    assert all(registry.has_streaming_handler(payload_type) is False for payload_type in _NEIGHBORS)


async def test_async_dispatch_payload_type_match_is_exact(sample_manifest: Manifest) -> None:
    """POST /asap uses dispatch_async, which repeats the raw-string lookup."""
    calls: list[str] = []
    registry = HandlerRegistry()
    registry.register(_REGISTERED, _sync_handler(calls))

    delivered = await registry.dispatch_async(_envelope(_REGISTERED), sample_manifest)

    assert delivered.payload_type == _REGISTERED
    assert calls == [_REGISTERED]
    for payload_type in _NEIGHBORS:
        with pytest.raises(HandlerNotFoundError) as caught:
            await registry.dispatch_async(_envelope(payload_type), sample_manifest)
        assert caught.value.payload_type == payload_type
    assert calls == [_REGISTERED]
    assert registry.has_handler(_REGISTERED) is True
    assert all(registry.has_handler(payload_type) is False for payload_type in _NEIGHBORS)
