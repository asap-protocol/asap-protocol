"""WebSocket route group: ``WS /asap/ws``.

ASAP JSON-RPC over WebSocket (same handlers as ``POST /asap``). OAuth2 is
enforced at WS acceptance when configured (B4/BUG #4); the HTTP middleware
stack does not run over WebSocket.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from asap.auth import OAuth2Middleware
from asap.transport.ws import (
    WS_CLOSE_GOING_AWAY,
    WS_CLOSE_REASON_SHUTDOWN,
    handle_websocket_connection,
)


def create_websocket_router() -> APIRouter:
    """Create the WebSocket router mounting ``WS /asap/ws``."""
    router = APIRouter(tags=["websocket"])

    @router.websocket("/asap/ws")
    async def websocket_asap(websocket: WebSocket) -> None:
        """ASAP JSON-RPC over WebSocket; same handlers as POST /asap."""
        app_state = websocket.app.state
        handler = app_state.request_handler
        ws_rate_limit: float | None = getattr(app_state, "websocket_message_rate_limit", 10.0)
        sla_subscribers: set[WebSocket] | None = getattr(app_state, "sla_breach_subscribers", None)
        oauth2_middleware: OAuth2Middleware | None = getattr(app_state, "oauth2_middleware", None)
        await handle_websocket_connection(
            websocket,
            handler,
            app_state.websocket_connections,
            ws_message_rate_limit=ws_rate_limit,
            sla_breach_subscribers=sla_subscribers,
            oauth2_middleware=oauth2_middleware,
        )

    return router


__all__ = ["create_websocket_router", "WS_CLOSE_GOING_AWAY", "WS_CLOSE_REASON_SHUTDOWN"]
