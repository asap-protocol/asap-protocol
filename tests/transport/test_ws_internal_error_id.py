"""WS internal-error frames must preserve the JSON-RPC ``id`` (CR#5).

``_send_internal_error_frame`` hardcoded ``"id": None`` even though the request
``response_id`` was in scope at both call sites, so clients could not correlate
internal-error failures back to the request that triggered them. The fix threads
``response_id`` through so the frame carries the request id when known.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import WebSocket

from asap.transport.ws._dispatch import _send_internal_error_frame


def _ws_stub(sent: list[str]) -> Any:
    ws = MagicMock(spec=WebSocket)

    async def _send_text(payload: str) -> None:
        sent.append(payload)

    ws.send_text = _send_text
    return ws


class TestWSInternalErrorIdPreserved:
    """CR#5: the request id is preserved on internal-error frames."""

    @pytest.mark.asyncio
    async def test_response_id_is_set_on_error_frame(self) -> None:
        """A known response_id is carried onto the internal-error frame."""
        sent: list[str] = []
        ws = _ws_stub(sent)

        await _send_internal_error_frame(ws, RuntimeError("boom"), response_id="req-99")

        assert len(sent) == 1
        frame = json.loads(sent[0])
        assert frame["id"] == "req-99"
        assert frame["error"]["code"] == -32603

    @pytest.mark.asyncio
    async def test_response_id_defaults_to_none_when_unknown(self) -> None:
        """When response_id is not provided, the frame falls back to ``None`` (no regression)."""
        sent: list[str] = []
        ws = _ws_stub(sent)

        await _send_internal_error_frame(ws, RuntimeError("boom"))

        assert len(sent) == 1
        frame = json.loads(sent[0])
        assert frame["id"] is None


class TestWSInternalErrorSanitized:
    """Production frames must not echo exception text (HTTP parity)."""

    @pytest.mark.asyncio
    async def test_production_frame_hides_exception_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Debug off: the client sees a generic message, not the exception."""
        monkeypatch.setattr("asap.transport.ws._dispatch.is_debug_mode", lambda: False)
        sent: list[str] = []

        await _send_internal_error_frame(
            _ws_stub(sent),
            RuntimeError("boom-secret"),
            response_id="req-1",
        )

        assert len(sent) == 1
        frame = json.loads(sent[0])
        assert frame["id"] == "req-1"
        assert frame["error"]["data"] == {"error": "Internal server error"}
        assert "boom-secret" not in sent[0]
        assert "RuntimeError" not in sent[0]

    @pytest.mark.asyncio
    async def test_debug_frame_includes_exception_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Debug on: operators can see the exception type and message."""
        monkeypatch.setattr("asap.transport.ws._dispatch.is_debug_mode", lambda: True)
        sent: list[str] = []

        await _send_internal_error_frame(
            _ws_stub(sent),
            RuntimeError("boom-secret"),
            response_id="req-1",
        )

        frame = json.loads(sent[0])
        assert frame["error"]["data"] == {
            "error": "boom-secret",
            "type": "RuntimeError",
        }
