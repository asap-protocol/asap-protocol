"""COMPAT-003: the A2A Agent Card served beside the ASAP manifest (v2.6.0).

Both documents come from one origin and describe one agent: the same name
and version, and the URN in the card's ASAP extension equals the manifest
``id``. The card route caches like the manifest route. Without a card the
route does not exist, and ``create_app`` refuses a card that is not signed
or that describes another agent, so such a card is never served.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    A2AKeyResolver,
    A2ATrustConfig,
    AgentCapabilities,
    AgentCard,
    CardSigner,
    build_signed_agent_card,
    manifest_to_agent_card,
    read_asap_extension,
    sign_agent_card,
    verify_agent_card,
)
from asap.discovery.wellknown import WELLKNOWN_AGENT_CARD_PATH, WELLKNOWN_MANIFEST_PATH
from asap.transport.server import create_app
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_KEY = ed25519.Ed25519PrivateKey.generate()
_TRUST = A2ATrustConfig(
    pinned_keys={"card-1": {**OKPKey.import_key(_KEY.public_key()).as_dict(), "kid": "card-1"}}
)


def _signed_card(**manifest_changes: Any) -> AgentCard:
    manifest = research_manifest(**manifest_changes)
    signers = [CardSigner(key=_KEY, kid="card-1")]
    return build_signed_agent_card(manifest, research_card_inputs(), signers=signers).card


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(research_manifest(), agent_card=_signed_card()))


async def test_card_and_manifest_from_one_origin_describe_the_same_agent(
    client: TestClient,
) -> None:
    manifest_json = client.get(WELLKNOWN_MANIFEST_PATH).json()
    card = AgentCard.model_validate(client.get(WELLKNOWN_AGENT_CARD_PATH).json())

    assert (card.name, card.version) == (manifest_json["name"], manifest_json["version"])
    extension = read_asap_extension(card)
    assert extension is not None and extension.agent_urn == manifest_json["id"]
    verification = await verify_agent_card(card, A2AKeyResolver(_TRUST))
    assert verification.signer_kids == ("card-1",)


def test_the_card_route_sets_json_and_caching_headers(client: TestClient) -> None:
    response = client.get(WELLKNOWN_AGENT_CARD_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "public, max-age=300"
    etag = response.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert client.get(WELLKNOWN_AGENT_CARD_PATH).headers["etag"] == etag


def test_a_matching_if_none_match_gets_304(client: TestClient) -> None:
    etag = client.get(WELLKNOWN_AGENT_CARD_PATH).headers["etag"]

    cached = client.get(WELLKNOWN_AGENT_CARD_PATH, headers={"If-None-Match": f'"x", {etag}'})
    stale = client.get(WELLKNOWN_AGENT_CARD_PATH, headers={"If-None-Match": '"other"'})

    assert (cached.status_code, cached.headers["etag"], cached.content) == (304, etag, b"")
    assert cached.headers["cache-control"] == "public, max-age=300"
    assert stale.status_code == 200


def test_without_a_card_the_route_does_not_exist() -> None:
    client = TestClient(create_app(research_manifest()))

    assert client.get(WELLKNOWN_AGENT_CARD_PATH).status_code == 404
    assert client.get(WELLKNOWN_MANIFEST_PATH).status_code == 200


def test_an_unsigned_card_is_refused() -> None:
    unsigned = manifest_to_agent_card(research_manifest(), research_card_inputs()).card

    with pytest.raises(ValueError, match="no signature"):
        create_app(research_manifest(), agent_card=unsigned)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"id": "urn:asap:agent:other"}, "agent_urn"),
        ({"name": "Other Agent"}, "name"),
        ({"version": "9.9.9"}, "version"),
    ],
)
def test_a_card_of_another_agent_is_refused(change: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        create_app(research_manifest(), agent_card=_signed_card(**change))


def test_a_card_without_the_asap_extension_is_refused() -> None:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    bare = card.model_copy(update={"capabilities": AgentCapabilities(streaming=True)})
    signed = sign_agent_card(bare, _KEY, kid="card-1")

    with pytest.raises(ValueError, match="ASAP extension"):
        create_app(research_manifest(), agent_card=signed)
