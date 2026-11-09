"""WebSocket frames whose JSON-RPC params are not an object.

Ack extraction runs before request validation. A string or list ``params``
must not raise ``AttributeError``; the client receives invalid params and the
socket stays open for the next frame.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.transport.jsonrpc import ASAP_METHOD, INVALID_PARAMS
from asap.transport.server import create_app

from .conftest import TEST_RATE_LIMIT_DEFAULT, NoRateLimitTestBase

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_PARAMS_MUST_BE_OBJECT = "JSON-RPC 'params' must be an object"


class TestWebSocketNonObjectParams(NoRateLimitTestBase):
    """Non-object JSON-RPC params on the WebSocket receive path."""

    @pytest.fixture
    def client(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: ASAPRateLimiter,
    ) -> TestClient:
        """Return a client whose HTTP limiter cannot trip these two frames."""
        app: FastAPI = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
        app.state.limiter = disable_rate_limiting
        return TestClient(app)

    def test_string_and_list_params_return_invalid_params(self, client: TestClient) -> None:
        """Each non-object params value returns -32602 without closing the socket."""
        frames: tuple[tuple[str, object], ...] = (
            ("params-string", "not-a-dict"),
            ("params-list", ["not", "an", "object"]),
        )
        with client.websocket_connect("/asap/ws") as websocket:
            for request_id, params in frames:
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": ASAP_METHOD,
                        "params": params,
                        "id": request_id,
                    }
                )
                websocket.send_text(body)
                payload = json.loads(websocket.receive_text())
                assert payload.get("id") == request_id
                error = payload["error"]
                assert error["code"] == INVALID_PARAMS
                assert error["message"] == "Invalid params"
                assert error["data"]["error"] == _PARAMS_MUST_BE_OBJECT
