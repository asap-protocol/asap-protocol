"""Plain HTTP A2A v1.0 transport for the ``a2a-bridge`` compliance profile.

The binding is the one the gateway
serves: JSON-RPC at ``<origin>/a2a``, the method ``SendMessage`` only, and the
header ``A2A-Version: 1.0``. The skill is selected in the message ``metadata``
under the ASAP extension URI, and the arguments are one ``data`` part.
The card and the ASAP manifest come from their
well-known paths on the same origin (A2A v1.0 §8.2; COMPAT-003). No ``a2a-sdk`` and no
``AgentCardIngestor``: the harness judges the raw answers and checks the card
itself. Each request has one
deadline for the whole answer and a body size cap, and never follows a redirect.

Example::

    transport = A2AHttpTransport("http://127.0.0.1:8788")
    card = await transport.fetch_card()
    reply = await transport.send_message("greet", {"name": "Ada"}, bearer_token=token)
    assert reply.rpc_result is not None
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from asap.adapters.a2a import ASAP_EXTENSION_URI

from asap_compliance.validators.http_answers import quoted_code
from asap_compliance.validators.http_exchange import HttpExchangeError, bounded_http_exchange

A2A_PROTOCOL_VERSION = "1.0"
A2A_JSONRPC_PATH = "/a2a"
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

    @property
    def rpc_result(self) -> dict[str, Any] | None:
        """The JSON-RPC ``result`` when the body answers this request, else ``None``."""
        result = self._rpc_member("result")
        return result if isinstance(result, dict) else None

    @property
    def rpc_error(self) -> dict[str, Any] | None:
        """The JSON-RPC ``error`` when the body answers this request, else ``None``."""
        error = self._rpc_member("error")
        return error if isinstance(error, dict) else None

    def _rpc_member(self, name: str) -> object:
        body = self.json_body or {}
        # type() and not ==: JSON true equals 1 in Python.
        answers_this = type(body.get("id")) is int and body.get("id") == self.request_id
        return body.get(name) if answers_this and body.get("jsonrpc") == "2.0" else None

    def describe(self) -> str:
        """Status, layer, and ``asap:*`` code or JSON-RPC error code, for a check message."""
        if self.rpc_result is not None:
            return f"HTTP {self.status_code}, a JSON-RPC result"
        if self.rpc_error is not None:
            code = self.rpc_error.get("code")
            shown = str(code) if type(code) is int else "with no integer code"
            return f"HTTP {self.status_code}, JSON-RPC error {shown}"
        description = (self.json_body or {}).get("error_description")
        return f"HTTP {self.status_code}, no JSON-RPC answer, {quoted_code(description)}"


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

    @property
    def a2a_url(self) -> str:
        """The URL of the JSON-RPC interface: ``<origin>/a2a``, never a URL from the card."""
        return f"{self._origin}{A2A_JSONRPC_PATH}"

    async def send_message(
        self,
        skill_id: str,
        arguments: Mapping[str, Any],
        *,
        bearer_token: str | None = None,
    ) -> A2AHttpResponse:
        """POST one ``SendMessage`` for *skill_id*; raise like :meth:`fetch_card`."""
        request_id = next(self._exchange_ids)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "A2A-Version": A2A_PROTOCOL_VERSION,
        }
        if bearer_token is not None:
            headers["Authorization"] = f"Bearer {bearer_token}"
        body = _send_message_body(request_id, skill_id, arguments)
        return await self._exchange(request_id, "POST", self.a2a_url, headers, body)

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


def _send_message_body(
    request_id: int, skill_id: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    message = {
        "role": "ROLE_USER",
        "messageId": str(uuid4()),
        "parts": [{"data": dict(arguments)}],
        "metadata": {ASAP_EXTENSION_URI: {"skill_id": skill_id}},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {"message": message},
    }


def _object(text: str) -> dict[str, Any] | None:
    """The JSON object of a body, or None (A2A v1.0 names no media type for the card)."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None
