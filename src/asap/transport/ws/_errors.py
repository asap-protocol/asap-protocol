"""WebSocket client error type.

Split into its own module so both :mod:`asap.transport.ws.client` and
:mod:`asap.transport.ws._recv` can import it without a circular dependency
(``_recv`` is a mixin of the transport but must not import the transport module
at its top). Re-exported from :mod:`asap.transport.ws`.
"""

from __future__ import annotations

from typing import Any


class WebSocketRemoteError(Exception):
    """Raised when the WS peer returns a JSON-RPC error frame."""

    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(f"WebSocket remote error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data or {}


__all__ = ["WebSocketRemoteError"]
