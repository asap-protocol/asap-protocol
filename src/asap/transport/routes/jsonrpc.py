"""JSON-RPC route group: ``POST /asap`` and ``POST /asap/stream``.

Handles single JSON-RPC requests, JSON-RPC batch arrays, and SSE streaming
of task chunks. The :class:`~asap.transport.server.ASAPRequestHandler` is
read from ``request.app.state.request_handler`` (set by ``create_app``).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse

from asap.transport.jsonrpc import (
    DEFAULT_MAX_BATCH_SIZE,
    INVALID_REQUEST,
    JsonRpcError,
    JsonRpcErrorResponse,
)

if TYPE_CHECKING:
    from asap.transport.server import ASAPRequestHandler


def _make_body_receive(body: bytes) -> Any:
    """Create an ASGI receive callable that returns the given body bytes."""
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _batch_error_response(reason: str) -> JSONResponse:
    """Build a canonical JSON-RPC error response for a batch-level failure.

    Uses :class:`JsonRpcErrorResponse` so batch errors match the canonical
    wire shape produced by :meth:`ASAPRequestHandler.build_error_response`.
    """
    error_response = JsonRpcErrorResponse(
        error=JsonRpcError.from_code(INVALID_REQUEST, data={"reason": reason}),
        id=None,
    )
    return JSONResponse(status_code=200, content=error_response.model_dump())


async def _handle_batch(
    request: Request,
    items: list[Any],
    handler: ASAPRequestHandler,
) -> JSONResponse:
    """Process a JSON-RPC batch request (array of requests).

    Empty batches and oversized batches return a single JSON-RPC error.
    Each sub-request is processed independently; failures do not abort the batch.
    """
    if len(items) == 0:
        return _batch_error_response("empty batch")

    max_size: int = getattr(request.app.state, "max_batch_size", DEFAULT_MAX_BATCH_SIZE)
    if len(items) > max_size:
        return _batch_error_response(f"batch size {len(items)} exceeds max {max_size}")

    request.app.state.limiter.check_n(request, len(items))

    async def _process_one(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            invalid_item = JsonRpcErrorResponse(
                error=JsonRpcError.from_code(INVALID_REQUEST),
                id=None,
            )
            return invalid_item.model_dump()
        sub_body = json.dumps(item).encode("utf-8")
        scope = dict(request.scope)
        sub_request = Request(scope, receive=_make_body_receive(sub_body))
        sub_response = await handler.handle_message(sub_request)
        if isinstance(sub_response, JSONResponse):
            raw = sub_response.body
            result: dict[str, Any] = json.loads(bytes(raw) if isinstance(raw, memoryview) else raw)
            return result
        if isinstance(sub_response, StreamingResponse):
            parts: list[bytes] = []
            async for chunk in sub_response.body_iterator:
                parts.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
            streamed: dict[str, Any] = json.loads(b"".join(parts))
            return streamed
        raw2 = sub_response.body
        fallback: dict[str, Any] = json.loads(bytes(raw2) if isinstance(raw2, memoryview) else raw2)
        return fallback

    results = await asyncio.gather(*[_process_one(item) for item in items])
    return JSONResponse(status_code=200, content=list(results))


def create_jsonrpc_router() -> APIRouter:
    """Create the JSON-RPC router with ``POST /asap`` and ``POST /asap/stream``."""
    router = APIRouter(tags=["jsonrpc"])

    @router.post("/asap", response_model=None)
    async def handle_asap_message(request: Request) -> Response:
        """Handle ASAP messages or JSON-RPC batch arrays.

        Uses ``request.body()`` (which Starlette caches) so that
        ``parse_json_body`` inside ``handle_message`` can re-read via
        ``request.stream()`` — Starlette yields the cached bytes.
        """
        handler: ASAPRequestHandler = request.app.state.request_handler
        body = await request.body()
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            request.app.state.limiter.check(request)
            return await handler.handle_message(request)

        if isinstance(parsed, list):
            return await _handle_batch(request, parsed, handler)

        request.app.state.limiter.check(request)
        return await handler.handle_message(request)

    @router.post("/asap/stream", response_model=None)
    async def handle_asap_stream(request: Request) -> Response:
        """Stream task chunks as Server-Sent Events (``Envelope`` JSON per ``data:`` line)."""
        handler: ASAPRequestHandler = request.app.state.request_handler
        request.app.state.limiter.check(request)
        return await handler.handle_stream(request)

    return router


__all__ = ["create_jsonrpc_router"]
