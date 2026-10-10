"""WebSocket per-connection rate limiting: flooding triggers disconnect, normal traffic unaffected."""

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.jsonrpc import ASAP_METHOD
from asap.transport.rate_limit import WebSocketTokenBucket
from asap.transport.server import create_app

from .conftest import NoRateLimitTestBase, TEST_RATE_LIMIT_DEFAULT

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter


def _make_jsonrpc_body(envelope: Envelope, request_id: str | int = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": ASAP_METHOD,
            "params": {"envelope": envelope.model_dump(mode="json")},
            "id": request_id,
        }
    )


class _FrozenMonotonic:
    """Controllable stand-in for ``time.monotonic`` so refill tests do not sleep."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestWebSocketTokenBucket:
    def test_consume_allows_under_limit(self) -> None:
        bucket = WebSocketTokenBucket(rate=2.0)
        assert bucket.consume(1) is True
        assert bucket.consume(1) is True
        assert bucket.consume(1) is False

    def test_refill_over_time_allows_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _FrozenMonotonic(1_000.0)
        monkeypatch.setattr("asap.transport.rate_limit.time.monotonic", clock)
        bucket = WebSocketTokenBucket(rate=10.0, capacity=10.0)
        for _ in range(10):
            assert bucket.consume(1) is True
        assert bucket.consume(1) is False
        clock.now += 0.2  # 2 tokens refill at rate=10
        assert bucket.consume(1) is True
        assert bucket.consume(1) is True
        assert bucket.consume(1) is False

    def test_consume_zero_always_allowed(self) -> None:
        bucket = WebSocketTokenBucket(rate=1.0)
        bucket.consume(1)
        assert bucket.consume(0) is True
        assert bucket.consume(1) is False

    def test_invalid_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="rate must be positive"):
            WebSocketTokenBucket(rate=0)
        with pytest.raises(ValueError, match="rate must be positive"):
            WebSocketTokenBucket(rate=-1.0)

    def test_rate_property(self) -> None:
        bucket = WebSocketTokenBucket(rate=5.0)
        assert bucket.rate == 5.0

    def test_batch_consume_is_all_or_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A burst larger than the balance is rejected and does not debit or refill twice."""
        clock = _FrozenMonotonic(1_000.0)
        monkeypatch.setattr("asap.transport.rate_limit.time.monotonic", clock)
        bucket = WebSocketTokenBucket(rate=1.0, capacity=4.0)

        assert bucket.consume(5) is False
        assert bucket.consume(4) is True
        clock.now = 1_002.0
        assert bucket.consume(3) is False
        assert bucket.consume(2) is True
        assert bucket.consume(1) is False

    def test_refill_caps_at_capacity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Idle time cannot accumulate more tokens than the configured capacity."""
        clock = _FrozenMonotonic(1_000.0)
        monkeypatch.setattr("asap.transport.rate_limit.time.monotonic", clock)
        bucket = WebSocketTokenBucket(rate=10.0, capacity=5.0)

        assert bucket.consume(5) is True
        clock.now = 1_100.0
        assert bucket.consume(5) is True
        assert bucket.consume(1) is False


class TestWebSocketRateLimitHandler(NoRateLimitTestBase):
    def test_message_flooding_triggers_disconnect(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: "ASAPRateLimiter",
    ) -> None:
        app = create_app(
            sample_manifest,
            rate_limit=TEST_RATE_LIMIT_DEFAULT,
            websocket_message_rate_limit=2.0,
        )
        app.state.limiter = disable_rate_limiting
        client = TestClient(app)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:flooder",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="c1",
                skill_id="echo",
                input={"msg": "hi"},
            ).model_dump(),
        )
        body = _make_jsonrpc_body(envelope)
        frames: list[dict] = []
        with client.websocket_connect("/asap/ws") as ws:
            ws.send_text(body)
            ws.send_text(body)
            ws.send_text(body)
            try:
                while True:
                    data = json.loads(ws.receive_text())
                    frames.append(data)
                    if data.get("error", {}).get("code") == -32001:
                        break
            except Exception:
                pass  # connection closed by server after rate limit
        rate_limit_errors = [f for f in frames if f.get("error", {}).get("code") == -32001]
        assert len(rate_limit_errors) >= 1
        assert "Rate limit exceeded" in rate_limit_errors[0]["error"].get("message", "")

    def test_normal_traffic_under_limit_unaffected(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: "ASAPRateLimiter",
    ) -> None:
        app = create_app(
            sample_manifest,
            rate_limit=TEST_RATE_LIMIT_DEFAULT,
            websocket_message_rate_limit=10.0,
        )
        app.state.limiter = disable_rate_limiting
        client = TestClient(app)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="c1",
                skill_id="echo",
                input={"msg": "normal"},
            ).model_dump(),
        )
        body = _make_jsonrpc_body(envelope)
        with client.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(body)
            response_text = websocket.receive_text()
        data = json.loads(response_text)
        assert "error" not in data or data.get("error", {}).get("code") != -32001
        assert "result" in data
        assert data["result"].get("envelope", {}).get("payload_type") == "task.response"

    def test_rate_limit_disabled_when_none(
        self,
        sample_manifest: Manifest,
        disable_rate_limiting: "ASAPRateLimiter",
    ) -> None:
        app = create_app(
            sample_manifest,
            rate_limit=TEST_RATE_LIMIT_DEFAULT,
            websocket_message_rate_limit=None,
        )
        app.state.limiter = disable_rate_limiting
        client = TestClient(app)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient=sample_manifest.id,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="c1",
                skill_id="echo",
                input={"msg": "no-limit"},
            ).model_dump(),
        )
        with client.websocket_connect("/asap/ws") as websocket:
            for req_id in range(5):
                websocket.send_text(_make_jsonrpc_body(envelope, request_id=req_id))
            for _ in range(5):
                response_text = websocket.receive_text()
                data = json.loads(response_text)
                assert data.get("error", {}).get("code") != -32001
                assert "result" in data
