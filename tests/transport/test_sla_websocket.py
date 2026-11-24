"""Tests for SLA breach WebSocket subscription and broadcast."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.economics.sla import SLABreach
from asap.transport.server import create_app
from asap.transport.ws import (
    SLA_BREACH_NOTIFICATION_METHOD,
    SLA_SUBSCRIBE_METHOD,
    SLA_UNSUBSCRIBE_METHOD,
    broadcast_sla_breach,
)

from .conftest import NoRateLimitTestBase, TEST_RATE_LIMIT_DEFAULT

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter


@pytest.fixture
def app(
    sample_manifest: Manifest,
    disable_rate_limiting: ASAPRateLimiter,
) -> FastAPI:
    app_instance = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
    app_instance.state.limiter = disable_rate_limiting
    return app_instance


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestSLAWebSocketSubscribeUnsubscribe(NoRateLimitTestBase):
    """Tests for sla.subscribe and sla.unsubscribe over WebSocket."""

    def test_sla_subscribe_returns_subscribed_true(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """JSON-RPC method sla.subscribe adds connection and returns subscribed: true."""
        with client.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": SLA_SUBSCRIBE_METHOD,
                        "id": 1,
                    }
                )
            )
            msg = json.loads(websocket.receive_text())
            assert msg.get("jsonrpc") == "2.0"
            assert msg.get("result", {}).get("subscribed") is True
            assert msg.get("id") == 1
            # While connected after subscribe, connection is in subscribers
            assert len(app.state.sla_breach_subscribers) >= 1

    def test_sla_unsubscribe_returns_unsubscribed_true(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """JSON-RPC method sla.unsubscribe removes connection and returns unsubscribed: true."""
        with client.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": SLA_SUBSCRIBE_METHOD,
                        "id": 1,
                    }
                )
            )
            json.loads(websocket.receive_text())
            websocket.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": SLA_UNSUBSCRIBE_METHOD,
                        "id": 2,
                    }
                )
            )
            msg = json.loads(websocket.receive_text())
        assert msg.get("result", {}).get("unsubscribed") is True
        assert msg.get("id") == 2


class TestBroadcastSlaBreach:
    """Unit tests for broadcast_sla_breach."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_subscribers(self) -> None:
        """broadcast_sla_breach sends a JSON-RPC notification to each subscriber."""
        breach = SLABreach(
            id="breach_1",
            agent_id="urn:asap:agent:test",
            breach_type="latency",
            threshold="500ms",
            actual="800ms",
            severity="warning",
            detected_at=datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc),
            resolved_at=None,
        )
        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()
        subscribers: set = set()
        subscribers.add(mock_ws)

        await broadcast_sla_breach(breach, subscribers)

        mock_ws.send_text.assert_called_once()
        call_arg = mock_ws.send_text.call_args[0][0]
        payload = json.loads(call_arg)
        assert payload.get("jsonrpc") == "2.0"
        assert payload.get("method") == SLA_BREACH_NOTIFICATION_METHOD
        assert "params" in payload
        assert "breach" in payload["params"]
        b = payload["params"]["breach"]
        assert b["id"] == "breach_1"
        assert b["agent_id"] == "urn:asap:agent:test"
        assert b["breach_type"] == "latency"
        assert b["threshold"] == "500ms"
        assert b["actual"] == "800ms"

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_connection(self) -> None:
        """If send_text raises, the connection is removed from subscribers."""
        breach = SLABreach(
            id="b2",
            agent_id="urn:asap:agent:a",
            breach_type="availability",
            threshold="99%",
            actual="97%",
            severity="warning",
            detected_at=datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc),
            resolved_at=None,
        )
        good_ws = MagicMock()
        good_ws.send_text = AsyncMock()
        bad_ws = MagicMock()
        bad_ws.send_text = AsyncMock(side_effect=OSError("closed"))
        subscribers: set = {good_ws, bad_ws}

        await broadcast_sla_breach(breach, subscribers)

        assert good_ws in subscribers
        assert bad_ws not in subscribers
        good_ws.send_text.assert_called_once()
