"""The webhook POST body and ``X-ASAP-Signature`` must be the same bytes.

Receivers recompute HMAC-SHA256 over the raw body. ``deliver`` signs the
canonical JSON it posts (sorted keys, no extra whitespace). A signature over
``json.dumps`` defaults, or over a different encoding than the POST, would
fail that check.
"""

from __future__ import annotations

import json
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from asap.transport.webhook import (
    X_ASAP_SIGNATURE_HEADER,
    WebhookDelivery,
    compute_signature,
    verify_signature,
)

_URL = "https://receiver.example.com/hook"
_SECRET = b"hook-secret"
_PAYLOAD: dict[str, Any] = {"z": 1, "a": {"y": 2, "b": 3}}


def _public_addrinfo() -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Return one public IPv4 result so URL validation does not use the network."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


async def test_deliver_signs_the_canonical_bytes_it_posts() -> None:
    """HMAC covers the compact sorted body, not insertion order or spaced JSON."""
    mock_client = AsyncMock()
    mock_client.post.return_value = httpx.Response(200)
    delivery = WebhookDelivery(secret=_SECRET, require_https=True, client=mock_client)
    dns = AsyncMock(return_value=_public_addrinfo())
    expected = json.dumps(_PAYLOAD, separators=(",", ":"), sort_keys=True).encode()

    with patch("asyncio.get_running_loop", return_value=MagicMock(getaddrinfo=dns)):
        result = await delivery.deliver(_URL, _PAYLOAD)

    assert result.success is True
    posted = mock_client.post.call_args.kwargs["content"]
    signature = mock_client.post.call_args.kwargs["headers"][X_ASAP_SIGNATURE_HEADER]
    assert posted == expected
    assert signature == compute_signature(posted, _SECRET)
    assert verify_signature(posted, _SECRET, signature) is True
    loose = json.dumps(_PAYLOAD).encode()
    assert posted != loose
    assert signature != compute_signature(loose, _SECRET)
