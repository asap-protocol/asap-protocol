"""WebSocket handshake must carry ``ASAPClient.auth_token`` (CR#3).

HTTP ``send()`` adds ``Authorization: Bearer …`` per request, but the WS
``connect()`` path never passed headers, so OAuth2-only deployments rejected
the handshake with close code 4401 even with a valid ``auth_token``. The fix
propagates ``auth_token`` to ``WebSocketTransport(extra_headers=...)`` which
forwards them to ``websockets.connect``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asap.transport.ws.client import WebSocketTransport


class TestWSHandshakeAuthToken:
    """CR#3: the Bearer token reaches ``websockets.connect`` kwargs."""

    @pytest.mark.asyncio
    async def test_extra_headers_reach_websockets_connect(self) -> None:
        """Headers passed to ``WebSocketTransport`` are forwarded to the connect call."""
        transport = WebSocketTransport(
            extra_headers={"Authorization": "Bearer test-token-123"},
            reconnect_on_disconnect=False,
        )

        captured: dict[str, Any] = {}

        class _FakeWS:
            async def close(self) -> None:
                return None

        async def _fake_connect(url: str, **kwargs: Any) -> Any:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeWS()

        with patch("asap.transport.ws.client.websockets.connect", _fake_connect):
            await transport.connect("ws://localhost:8000/asap/ws")

        assert captured["kwargs"].get("extra_headers") == {"Authorization": "Bearer test-token-123"}
        await transport.close()

    @pytest.mark.asyncio
    async def test_no_extra_headers_when_none_provided(self) -> None:
        """When no headers are configured, ``extra_headers`` is omitted from connect kwargs."""
        transport = WebSocketTransport(reconnect_on_disconnect=False)

        captured: dict[str, Any] = {}

        class _FakeWS:
            async def close(self) -> None:
                return None

        async def _fake_connect(url: str, **kwargs: Any) -> Any:
            captured["kwargs"] = kwargs
            return _FakeWS()

        with patch("asap.transport.ws.client.websockets.connect", _fake_connect):
            await transport.connect("ws://localhost:8000/asap/ws")

        assert "extra_headers" not in captured["kwargs"]
        await transport.close()

    @pytest.mark.asyncio
    async def test_asap_client_propagates_auth_token_to_ws_transport(self) -> None:
        """``ASAPClient(auth_token=...)`` builds a WS transport with the Bearer header."""
        from asap.transport.client._core import ASAPClient

        captured: dict[str, Any] = {}

        original_init = WebSocketTransport.__init__

        def _spy_init(self: WebSocketTransport, *args: Any, **kwargs: Any) -> None:
            captured["extra_headers"] = kwargs.get("extra_headers")
            original_init(self, *args, **kwargs)

        class _FakeWS:
            async def close(self) -> None:
                return None

        async def _fake_connect(url: str, **kwargs: Any) -> Any:
            return _FakeWS()

        with (
            patch("asap.transport.ws.client.websockets.connect", _fake_connect),
            patch.object(WebSocketTransport, "__init__", _spy_init),
            patch("httpx.AsyncClient", MagicMock(return_value=MagicMock(aclose=AsyncMock()))),
        ):
            client = ASAPClient(
                base_url="http://localhost:8000",
                auth_token="secret-token",
                transport_mode="websocket",
                require_https=False,
            )
            async with client:
                assert captured["extra_headers"] == {"Authorization": "Bearer secret-token"}

    @pytest.mark.asyncio
    async def test_asap_client_auto_mode_propagates_auth_token_to_ws_transport(self) -> None:
        """``transport_mode=\"auto\"`` with ws:// must carry ``auth_token`` on the handshake (#246)."""
        from asap.transport.client._core import ASAPClient

        captured: dict[str, Any] = {}

        original_init = WebSocketTransport.__init__

        def _spy_init(self: WebSocketTransport, *args: Any, **kwargs: Any) -> None:
            captured["extra_headers"] = kwargs.get("extra_headers")
            original_init(self, *args, **kwargs)

        class _FakeWS:
            async def close(self) -> None:
                return None

        async def _fake_connect(url: str, **kwargs: Any) -> Any:
            captured["connect_url"] = url
            return _FakeWS()

        with (
            patch("asap.transport.ws.client.websockets.connect", _fake_connect),
            patch.object(WebSocketTransport, "__init__", _spy_init),
            patch("httpx.AsyncClient", MagicMock(return_value=MagicMock(aclose=AsyncMock()))),
        ):
            client = ASAPClient(
                base_url="ws://localhost:8000/asap/ws",
                auth_token="auto-mode-token",
                transport_mode="auto",
                require_https=False,
            )
            async with client:
                assert captured["extra_headers"] == {"Authorization": "Bearer auto-mode-token"}
                assert captured["connect_url"] == "ws://localhost:8000/asap/ws"
