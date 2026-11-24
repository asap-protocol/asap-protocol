"""Plain HTTP A2A v1.0 transport for the ``a2a-bridge`` compliance profile.

The card and the ASAP manifest come from their well-known paths on the
gateway's origin (A2A v1.0 §8.2; COMPAT-003). No ``a2a-sdk`` and no
``AgentCardIngestor``: the harness judges the raw answers and checks the card
itself. Each request has one
deadline for the whole answer and a body size cap, and never follows a redirect.

Example::

    transport = A2AHttpTransport("http://127.0.0.1:8788")
    card = await transport.fetch_card()
    assert card.status_code == 200 and card.json_body is not None
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any

import httpx

from asap_compliance.validators.http_exchange import HttpExchangeError, bounded_http_exchange

AGENT_CARD_PATH = "/.well-known/agent-card.json"
ASAP_MANIFEST_PATH = "/.well-known/asap/manifest.json"
# The card fetch limit of asap.adapters.a2a ingest (the guarded fetch default).
MAX_RESPONSE_BYTES = 1_048_576


class A2AHttpTransportError(HttpExchangeError):
    """A request to the gateway got no complete HTTP answer (network error, timeout, or size)."""


@dataclass(frozen=True)
class A2AHttpResponse:
    """One HTTP answer of the gateway, as it sent it; ``request_id`` numbers the exchange."""

    request_id: int
    status_code: int
    headers: httpx.Headers
    body_text: str
    json_body: dict[str, Any] | None


class A2AHttpTransport:
    """Talk to one A2A gateway origin over plain HTTP; never follow a redirect."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._origin = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport
        self._exchange_ids = itertools.count(1)

    async def fetch_card(self) -> A2AHttpResponse:
        """GET the Agent Card; raise :class:`A2AHttpTransportError` if no full answer came."""
        return await self._get(AGENT_CARD_PATH)

    async def fetch_manifest(self) -> A2AHttpResponse:
        """GET the ASAP manifest on the same origin; raise like :meth:`fetch_card`."""
        return await self._get(ASAP_MANIFEST_PATH)

    async def _get(self, path: str) -> A2AHttpResponse:
        url = f"{self._origin}{path}"
        exchange_id = next(self._exchange_ids)
        return await self._exchange(exchange_id, "GET", url, {"Accept": "application/json"}, None)

    async def _exchange(
        self,
        exchange_id: int,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
    ) -> A2AHttpResponse:
        answer = await bounded_http_exchange(
            method,
            url,
            headers=headers,
            json_body=json_body,
            timeout_seconds=self._timeout_seconds,
            max_bytes=MAX_RESPONSE_BYTES,
            http_transport=self._http_transport,
            where=f"{method} {url}",
            error_type=A2AHttpTransportError,
        )
        text = answer.body.decode("utf-8", errors="replace")
        return A2AHttpResponse(exchange_id, answer.status_code, answer.headers, text, _object(text))


def _object(text: str) -> dict[str, Any] | None:
    """The JSON object of a body, or None (A2A v1.0 names no media type for the card)."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None
