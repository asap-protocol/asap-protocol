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

from asap.adapters.a2a import (
    AgentCard,
    AgentCardInputs,
    AgentInterface,
    CardSigner,
    build_signed_agent_card,
    sign_agent_card,
)
from asap.models.entities import Capability, Endpoint, Manifest, Skill

BASE_URL = "https://gateway.example"
AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
CARD_KID = "gateway-card-1"
PROBE_TOKENS_PATH = "/compliance/probe-tokens"
CARD_KEY = Ed25519PrivateKey.generate()
CARD_PUBLIC_JWK = {**OKPKey.import_key(CARD_KEY.public_key()).as_dict(), "kid": CARD_KID}
OTHER_AGENT_URN = "urn:asap:agent:someone-else"
_A2A_URL = f"{BASE_URL}/a2a"
# What an A2A v0.3 card looks like: a top-level url and protocolVersion.
_V03_CARD = {"name": "Old", "url": _A2A_URL, "protocolVersion": "0.3.0", "version": "1.0.0"}


class Fault(StrEnum):
    """One way to break the gateway's behavior."""

    NO_CARD = "no_card"
    CARD_NOT_JSON = "card_not_json"
    CARD_V03 = "card_v03"
    UNSIGNED_CARD = "unsigned_card"
    TAMPERED_CARD = "tampered_card"
    # Signed with the key that the checker trusts for AGENT_URN, for another agent URN.
    CARD_FOR_ANOTHER_AGENT = "card_for_another_agent"
    CARD_WITHOUT_EXTENSION = "card_without_extension"


def demo_manifest(agent_urn: str = AGENT_URN, version: str = "1.0.0") -> Manifest:
    """The manifest of ``examples/a2a_gateway``, at :data:`BASE_URL`."""
    skills = [
        Skill(id="echo", description="Return the text it receives"),
        Skill(id="greet", description="Greet a person by name"),
    ]
    return Manifest(
        id=agent_urn,
        name="A2A Gateway Demo",
        version=version,
        description="An ASAP agent reached through a reference A2A v1.0 gateway",
        capabilities=Capability(skills=skills),
        endpoints=Endpoint(asap=f"{BASE_URL}/asap"),
    )


def signed_card(
    manifest: Manifest | None = None, *, interface_url: str = _A2A_URL
) -> dict[str, Any]:
    """The JSON of a card signed by :data:`CARD_KEY`, as ``create_app`` serves it."""
    interface = AgentInterface(
        url=interface_url, protocol_binding="JSONRPC", protocol_version="1.0"
    )
    inputs = AgentCardInputs(
        interfaces=[interface],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skill_tags={"echo": ["demo"], "greet": ["demo"]},
    )
    signers = [CardSigner(key=CARD_KEY, kid=CARD_KID)]
    card = build_signed_agent_card(manifest or demo_manifest(), inputs, signers=signers).card
    return card.model_dump(mode="json")


def _signed_without_extension(card: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in card.items() if key != "signatures"}
    body["capabilities"] = {"streaming": False}
    unsigned = AgentCard.model_validate(body)
    return sign_agent_card(unsigned, CARD_KEY, kid=CARD_KID).model_dump(mode="json")


_CARD = signed_card()
_CARDS_BY_FAULT: dict[Fault, dict[str, Any]] = {
    Fault.CARD_V03: _V03_CARD,
    Fault.UNSIGNED_CARD: {key: value for key, value in _CARD.items() if key != "signatures"},
    Fault.TAMPERED_CARD: {**_CARD, "description": "Changed in transit"},
    Fault.CARD_FOR_ANOTHER_AGENT: signed_card(demo_manifest(OTHER_AGENT_URN)),
    Fault.CARD_WITHOUT_EXTENSION: _signed_without_extension(_CARD),
}


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
        card = next((_CARDS_BY_FAULT[f] for f in self.faults if f in _CARDS_BY_FAULT), _CARD)
        return httpx.Response(200, json=card)
