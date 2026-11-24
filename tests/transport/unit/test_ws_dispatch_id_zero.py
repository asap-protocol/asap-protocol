"""WebSocket dispatch must pass JSON-RPC id 0 through to the result frame.

The HTTP echo covers ``_build_success_response``. This covers the separate
``jsonrpc_id if jsonrpc_id is not None else ""`` assignment on the socket path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from opentelemetry import context

from asap.transport.ws._actions import WSCloseAction
from asap.transport.ws._dispatch import _dispatch_ws_envelope


class _PrepareHandler:
    """Handler double whose prepare step returns one prepared request."""

    def __init__(self, prepared: SimpleNamespace) -> None:
        self._prepared = prepared

    async def _prepare_request(
        self,
        _request: object,
        _start: float,
        *,
        received_log_event: str,
    ) -> SimpleNamespace:
        """Return the prepared request without running the HTTP pipeline."""
        assert received_log_event == "asap.request.ws_received"
        return self._prepared


@pytest.mark.asyncio
async def test_ws_dispatch_passes_numeric_id_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Id 0 is forwarded. A falsy check would forward an empty string."""
    captured: list[str | int] = []

    async def capture_dispatch(
        _websocket: object,
        _handler: object,
        _prepared: object,
        response_id: str | int,
    ) -> None:
        captured.append(response_id)

    monkeypatch.setattr("asap.transport.ws._dispatch._run_ws_dispatch", capture_dispatch)
    token = context.attach(context.get_current())
    prepared = SimpleNamespace(envelope=object(), ctx=SimpleNamespace(), trace_token=token)
    socket = SimpleNamespace(scope={"headers": [], "path": "/asap/ws"})
    payload = {"jsonrpc": "2.0", "method": "asap.send", "params": {}, "id": 0}

    action = await _dispatch_ws_envelope(
        socket,
        _PrepareHandler(prepared),
        json.dumps(payload),
        payload,
        None,
    )

    assert action is WSCloseAction.CONTINUE
    assert captured == [0]
