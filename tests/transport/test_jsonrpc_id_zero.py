"""JSON-RPC id 0 is a real identifier and must be echoed.

``request_id or ""`` and ``if request_id`` both treat 0 as missing. Existing
correlation tests use non-empty strings, so they stay green.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.jsonrpc import METHOD_NOT_FOUND
from asap.transport.server import create_app

from .conftest import TEST_RATE_LIMIT_DEFAULT, NoRateLimitTestBase

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter


def _echo_request(request_id: int) -> dict[str, object]:
    """JSON-RPC body for the echo skill with the given id."""
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv_01",
            skill_id="echo",
            input={"text": "hello"},
        ).model_dump(),
    )
    return {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope.model_dump(mode="json")},
        "id": request_id,
    }


class TestJsonRpcIdZero(NoRateLimitTestBase):
    """POST /asap keeps numeric id 0 on success and on method-not-found."""

    def test_jsonrpc_success_echoes_numeric_id_zero(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: ASAPRateLimiter,
    ) -> None:
        """A completed echo response uses id 0, not an empty string."""
        app = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
        app.state.limiter = disable_rate_limiting
        response = TestClient(app).post("/asap", json=_echo_request(0))
        body = response.json()

        assert response.status_code == 200
        assert body["id"] == 0
        assert "result" in body
        assert "error" not in body

    def test_jsonrpc_error_keeps_numeric_id_zero(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: ASAPRateLimiter,
    ) -> None:
        """Method-not-found still correlates to id 0 instead of null."""
        app = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
        app.state.limiter = disable_rate_limiting
        response = TestClient(app).post(
            "/asap",
            json={
                "jsonrpc": "2.0",
                "method": "asap.sendextra",
                "params": {},
                "id": 0,
            },
        )
        body = response.json()

        assert response.status_code == 200
        assert body["id"] == 0
        assert body["error"]["code"] == METHOD_NOT_FOUND
