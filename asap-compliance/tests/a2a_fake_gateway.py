"""Fake A2A gateway for the ``a2a-bridge`` profile tests.

It answers like ``examples/a2a_gateway`` served with ``--compliance``: the
card at the well-known path, the ASAP manifest beside it, a probe endpoint, and
``POST /a2a`` in the layers of its README's refusal table. Each ``Fault``
breaks one behavior, so each profile check has a passing and a failing server.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc.jwk import OKPKey

BASE_URL = "https://gateway.example"
AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
CARD_KID = "gateway-card-1"
PROBE_TOKENS_PATH = "/compliance/probe-tokens"
CARD_KEY = Ed25519PrivateKey.generate()
CARD_PUBLIC_JWK = {**OKPKey.import_key(CARD_KEY.public_key()).as_dict(), "kid": CARD_KID}
_CARD: dict[str, Any] = {"name": "A2A Gateway Demo", "version": "1.0.0"}


class Fault(StrEnum):
    """One way to break the gateway's behavior."""

    NO_CARD = "no_card"
    CARD_NOT_JSON = "card_not_json"


class FakeA2AGateway:
    """Handler for ``httpx.MockTransport``; ``requests`` lists each path asked for."""

    def __init__(self, *faults: Fault) -> None:
        self.faults = frozenset(faults)
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == "/.well-known/agent-card.json":
            return self._card()
        return httpx.Response(404, json={"detail": "Not Found"})

    def _card(self) -> httpx.Response:
        if Fault.NO_CARD in self.faults:
            return httpx.Response(404, json={"detail": "Not Found"})
        if Fault.CARD_NOT_JSON in self.faults:
            return httpx.Response(200, content=b"<html>card</html>")
        return httpx.Response(200, json=_CARD)
