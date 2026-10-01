"""Registration rules for SSE and WebSocket streaming handlers.

Sync ``register`` and ``register_streaming_handler`` share a payload type but
keep separate maps. A failed streaming registration must not replace a live
generator, and a plain coroutine must not be accepted as a stream handler.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import patch

import pytest

from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.handlers import (
    HandlerNotFoundError,
    HandlerRegistry,
    create_echo_handler,
    validate_streaming_handler,
)


def _task_envelope() -> Envelope:
    """Build a task.request envelope the echo handler can dispatch."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv-stream-register",
            skill_id="echo",
            input={"message": "hello"},
        ).model_dump(),
    )


async def _yield_request(envelope: Envelope, manifest: Manifest) -> AsyncIterator[Envelope]:
    """Async generator with the required (envelope, manifest) signature."""
    yield envelope


async def _yield_with_receiver(
    self: object, envelope: Envelope, manifest: Manifest
) -> AsyncIterator[Envelope]:
    """Unbound async generator whose first parameter is named self."""
    yield envelope


async def _coroutine_not_generator(envelope: Envelope, manifest: Manifest) -> Envelope:
    """Async function that returns once and never yields."""
    return envelope


def _sync_generator(envelope: Envelope, manifest: Manifest) -> Any:
    """Sync generator; streaming registration must reject it."""
    yield envelope


async def _one_parameter(envelope: Envelope) -> AsyncIterator[Envelope]:
    """Async generator with the wrong arity."""
    yield envelope


class TestValidateStreamingHandler:
    """validate_streaming_handler accepts only async generators of the right shape."""

    def test_accepts_two_parameter_async_generator(self) -> None:
        """(envelope, manifest) async generators pass."""
        validate_streaming_handler(_yield_request)

    def test_accepts_three_parameter_when_first_is_self(self) -> None:
        """(self, envelope, manifest) is allowed for methods defined as functions."""
        validate_streaming_handler(_yield_with_receiver)

    def test_rejects_non_callable(self) -> None:
        """Non-callables fail before signature inspection."""
        with pytest.raises(TypeError, match="callable"):
            validate_streaming_handler(cast(Any, "not-a-handler"))

    def test_rejects_async_coroutine_without_yield(self) -> None:
        """A plain async def is not a streaming handler."""
        with pytest.raises(TypeError, match="async generator"):
            validate_streaming_handler(cast(Any, _coroutine_not_generator))

    def test_rejects_sync_generator(self) -> None:
        """Sync generators cannot drive SSE or WebSocket streaming."""
        with pytest.raises(TypeError, match="async generator"):
            validate_streaming_handler(cast(Any, _sync_generator))

    def test_rejects_wrong_parameter_count(self) -> None:
        """Arity other than (envelope, manifest) or (self, envelope, manifest) fails."""
        with pytest.raises(TypeError, match="1 parameters"):
            validate_streaming_handler(cast(Any, _one_parameter))

    def test_rejects_when_signature_inspection_fails(self) -> None:
        """Uninspectable callables fail closed."""
        with (
            patch.object(inspect, "signature", side_effect=ValueError("unsupported callable")),
            pytest.raises(TypeError, match="could not be inspected"),
        ):
            validate_streaming_handler(_yield_request)


class TestStreamingHandlerRegistry:
    """Streaming registration stays independent of the unary handler map."""

    async def test_sync_and_streaming_handlers_dispatch_independently(
        self, sample_manifest: Manifest
    ) -> None:
        """POST dispatch uses the sync handler; stream dispatch uses the generator."""
        registry = HandlerRegistry()
        envelope = _task_envelope()
        registry.register("task.request", create_echo_handler())
        registry.register_streaming_handler("task.request", _yield_request)

        sync_response = registry.dispatch(envelope, sample_manifest)
        streamed = [
            item async for item in registry.dispatch_stream_async(envelope, sample_manifest)
        ]

        assert sync_response.payload_type == "task.response"
        assert streamed == [envelope]
        assert registry.has_handler("task.request")
        assert registry.has_streaming_handler("task.request")

    async def test_replacing_one_map_leaves_the_other(self, sample_manifest: Manifest) -> None:
        """Overriding the sync handler does not drop the streaming handler."""
        registry = HandlerRegistry()
        envelope = _task_envelope()
        registry.register("task.request", create_echo_handler())
        registry.register_streaming_handler("task.request", _yield_request)

        def identity(request: Envelope, manifest: Manifest) -> Envelope:
            return request

        registry.register("task.request", identity)
        streamed = [
            item async for item in registry.dispatch_stream_async(envelope, sample_manifest)
        ]
        sync_response = registry.dispatch(envelope, sample_manifest)

        assert streamed == [envelope]
        assert sync_response is envelope

    async def test_failed_streaming_replace_keeps_previous_generator(
        self, sample_manifest: Manifest
    ) -> None:
        """A rejected override must not wipe the handler already serving streams."""
        registry = HandlerRegistry()
        envelope = _task_envelope()
        registry.register_streaming_handler("task.request", _yield_request)

        with pytest.raises(TypeError, match="async generator"):
            registry.register_streaming_handler("task.request", cast(Any, _coroutine_not_generator))

        streamed = [
            item async for item in registry.dispatch_stream_async(envelope, sample_manifest)
        ]
        assert streamed == [envelope]
        assert registry.has_streaming_handler("task.request")

    async def test_missing_streaming_handler_names_payload_type(
        self, sample_manifest: Manifest
    ) -> None:
        """Stream dispatch without a generator raises HandlerNotFoundError."""
        registry = HandlerRegistry()
        registry.register("task.request", create_echo_handler())
        envelope = _task_envelope()

        with pytest.raises(HandlerNotFoundError) as exc_info:
            async for _item in registry.dispatch_stream_async(envelope, sample_manifest):
                pass

        assert exc_info.value.payload_type == "task.request"
        assert registry.has_handler("task.request")
        assert registry.has_streaming_handler("task.request") is False
