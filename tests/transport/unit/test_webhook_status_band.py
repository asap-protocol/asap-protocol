"""Webhook delivery treats only HTTP 200-299 as success.

``deliver`` returns a ``WebhookResult``. It does not write a delivery record.
199 is below the inclusive lower bound, 200 and 299 are inside it, and 300
is a redirect, so it is not a success.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asap.transport.webhook import WebhookDelivery

_URL = "https://receiver.example.com/hook"
_PAYLOAD: dict[str, str] = {"event": "task.completed"}


def _public_addrinfo() -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.mark.parametrize(
    ("status_code", "delivered"),
    [(199, False), (200, True), (299, True), (300, False)],
)
async def test_deliver_success_is_exclusive_to_the_2xx_band(
    status_code: int,
    delivered: bool,
) -> None:
    """Success is ``200 <= status < 300`` on the result ``deliver`` returns."""
    delivery = WebhookDelivery(secret=None, require_https=True)
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(status_code)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    dns = AsyncMock(return_value=_public_addrinfo())

    with (
        patch("asyncio.get_running_loop", return_value=MagicMock(getaddrinfo=dns)),
        patch("asap.transport.webhook.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await delivery.deliver(_URL, _PAYLOAD)

    assert result.success is delivered
    assert result.status_code == status_code
