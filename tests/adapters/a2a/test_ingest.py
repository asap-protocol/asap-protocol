"""Ingesting an A2A Agent Card: fetch, verify, then map.

Every failure is a typed error, and a card that fails a step is never mapped
and never cached: the next ingest of the same URL fetches again, which the
server's request count shows.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    A2ACanonicalFormError,
    A2ACardFetchError,
    A2ACardVerificationError,
    A2AInvalidSignatureError,
    A2AMappingError,
    A2AMappingInputError,
    A2ANoTrustedSignatureError,
    A2ATrustConfig,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    A2AUnsupportedVersionError,
    AgentCard,
    AgentCardIngestor,
    manifest_to_agent_card,
    sign_agent_card,
)
from asap.models.entities import Endpoint
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import sign_with_joserfc

_CARD_URL = "https://agent.example.com/.well-known/agent-card.json"
_KEY = ed25519.Ed25519PrivateKey.generate()
_TRUST = A2ATrustConfig(
    pinned_keys={"k1": {**OKPKey.import_key(_KEY.public_key()).as_dict(), "kid": "k1"}}
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _CardServer:
    """Serves one JSON document at every URL and counts the requests."""

    def __init__(self, document: Any, status: int = 200) -> None:
        self.requests = 0
        self.transport = httpx.MockTransport(self._handle)
        self._document, self._status = document, status

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(self._status, json=self._document)


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _ingestor(
    server: _CardServer, trust: A2ATrustConfig = _TRUST, **options: Any
) -> AgentCardIngestor:
    return AgentCardIngestor(trust, dns_resolver=_public_dns, transport=server.transport, **options)


def _signed_card_json(**changes: Any) -> dict[str, Any]:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    signed: dict[str, Any] = sign_agent_card(card, _KEY, kid="k1").model_dump(mode="json")
    return {**signed, **changes}


async def test_a_signed_card_is_fetched_verified_and_mapped() -> None:
    server = _CardServer(_signed_card_json())

    result = await _ingestor(server).ingest(_CARD_URL)

    assert result.manifest == research_manifest()
    assert (result.verified, result.signer_kids, result.uncovered) == (True, ("k1",), ())
    assert "supportedInterfaces" in [dropped.field for dropped in result.dropped]
    assert server.requests == 1


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (_signed_card_json(description="Tampered after signing"), A2AInvalidSignatureError),
        (_signed_card_json(signatures=None), A2AUnsignedCardError),
        ({**_signed_card_json(), "signatures": []}, A2AUnsignedCardError),
    ],
    ids=["tampered", "unsigned", "empty signatures"],
)
async def test_a_card_that_fails_verification_is_a_typed_error_and_not_mapped(
    document: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        await _ingestor(_CardServer(document)).ingest(_CARD_URL)


async def test_a_card_from_an_unknown_key_fails_as_no_trusted_signature() -> None:
    with pytest.raises(A2ANoTrustedSignatureError):
        await _ingestor(_CardServer(_signed_card_json()), A2ATrustConfig()).ingest(_CARD_URL)


async def test_an_unsigned_card_under_allow_unsigned_yields_a_manifest_marked_unverified() -> None:
    server = _CardServer(_signed_card_json(signatures=None))

    result = await _ingestor(server, A2ATrustConfig(policy="allow_unsigned")).ingest(_CARD_URL)

    assert result.manifest == research_manifest()
    assert (result.verified, result.signer_kids) == (False, ())


@pytest.mark.parametrize(
    ("url", "status", "refusal"),
    [
        ("http://agent.example.com/.well-known/agent-card.json", 200, "scheme_not_https"),
        (_CARD_URL, 404, "http_status"),
    ],
)
async def test_a_refused_fetch_is_a_typed_error(url: str, status: int, refusal: str) -> None:
    with pytest.raises(A2ACardFetchError) as raised:
        await _ingestor(_CardServer({}, status)).ingest(url)

    assert (raised.value.refusal, raised.value.url) == (refusal, url)
    assert raised.value.code == "asap:adapters/a2a/card_fetch"


async def test_a_card_with_an_empty_required_field_fails_as_a_typed_error() -> None:
    server = _CardServer(_signed_card_json(description=""))

    with pytest.raises(A2AMappingError) as raised:
        await _ingestor(server).ingest(_CARD_URL)

    assert raised.value.fields == ("description",)


async def test_a_v03_card_fails_as_unsupported() -> None:
    with pytest.raises(A2AUnsupportedVersionError):
        await _ingestor(_CardServer(load_card_fixture("v03_card.json"))).ingest(_CARD_URL)


async def test_a_verified_card_is_cached_for_its_lifetime_then_fetched_again() -> None:
    server, clock = _CardServer(_signed_card_json()), _Clock()
    ingestor = _ingestor(server, cache_ttl_seconds=60, clock=clock)

    await ingestor.ingest(_CARD_URL)
    clock.now = 59
    await ingestor.ingest(_CARD_URL)
    assert server.requests == 1

    clock.now = 60
    await ingestor.ingest(_CARD_URL)
    assert server.requests == 2


@pytest.mark.parametrize(
    ("document", "trust"),
    [
        (_signed_card_json(description="Tampered after signing"), _TRUST),
        (_signed_card_json(), A2ATrustConfig()),
        (_signed_card_json(signatures=None), _TRUST),
    ],
    ids=["tampered", "unknown key", "unsigned"],
)
async def test_a_card_that_fails_verification_leaves_no_cache_entry(
    document: dict[str, Any], trust: A2ATrustConfig
) -> None:
    server = _CardServer(document)
    ingestor = _ingestor(server, trust)

    for _ in range(2):
        with pytest.raises(A2ACardVerificationError):
            await ingestor.ingest(_CARD_URL)

    assert server.requests == 2


async def test_a_card_that_fails_to_map_is_not_cached() -> None:
    card_json = load_card_fixture("full_card.json")
    card_json.pop("signatures")
    server = _CardServer(card_json)
    ingestor = _ingestor(server, A2ATrustConfig(policy="allow_unsigned"))

    with pytest.raises(A2AMappingInputError):
        await ingestor.ingest(_CARD_URL)
    result = await ingestor.ingest(
        _CARD_URL,
        agent_id="urn:asap:agent:research",
        endpoints=Endpoint(asap="https://agent.example.com/asap"),
    )

    assert result.manifest.id == "urn:asap:agent:research"
    assert server.requests == 2


_SCHEMA_PATH = "capabilities.extensions[0].params.skill_schemas.web_research.input_schema"


def _enum_added_after_signing() -> dict[str, Any]:
    card_json = _signed_card_json()
    params = card_json["capabilities"]["extensions"][0]["params"]
    params["skill_schemas"]["web_research"]["input_schema"]["properties"]["query"]["enum"] = []
    return card_json


def _inexact_number_signed_without_the_coverage_check() -> dict[str, Any]:
    card_json = _signed_card_json(signatures=None)
    card_json["capabilities"]["extensions"][0]["params"]["skill_schemas"]["web_research"][
        "input_schema"
    ]["maximum"] = 2**53 + 1
    jose_key = OKPKey.import_key(_KEY)
    signed = sign_with_joserfc(AgentCard.model_validate(card_json), jose_key, alg="EdDSA", kid="k1")
    return signed.model_dump(mode="json")


@pytest.mark.parametrize(
    ("document", "path"),
    [
        (_enum_added_after_signing(), f"{_SCHEMA_PATH}.properties.query.enum"),
        (_inexact_number_signed_without_the_coverage_check(), f"{_SCHEMA_PATH}.maximum"),
    ],
    ids=["empty enum added after signing", "integer beyond 2^53"],
)
async def test_a_signed_card_with_uncovered_asap_params_is_refused_and_not_cached(
    document: dict[str, Any], path: str
) -> None:
    server = _CardServer(document)
    ingestor = _ingestor(server)

    for _ in range(2):
        with pytest.raises(A2AUncoveredExtensionError) as raised:
            await ingestor.ingest(_CARD_URL)

    assert raised.value.paths == (path,)
    assert raised.value.code == "asap:adapters/a2a/uncovered_extension"
    assert server.requests == 2


async def test_uncovered_values_outside_the_asap_params_are_reported_and_never_mapped() -> None:
    card_json = _signed_card_json()
    card_json["securitySchemes"] = {"mtls": {"mtlsSecurityScheme": {}}}

    result = await _ingestor(_CardServer(card_json)).ingest(_CARD_URL)

    assert (result.verified, result.uncovered) == (True, ("securitySchemes",))
    assert result.manifest == research_manifest()


async def test_an_unsigned_card_with_no_canonical_form_fails_typed_and_is_not_cached() -> None:
    deep: dict[str, Any] = {"leaf": 1}
    for _ in range(300):
        deep = {"next": deep}
    card_json = _signed_card_json(signatures=None)
    card_json["capabilities"]["extensions"].append({"uri": "urn:example:deep", "params": deep})
    server = _CardServer(card_json)
    ingestor = _ingestor(server, A2ATrustConfig(policy="allow_unsigned"))

    for _ in range(2):
        with pytest.raises(A2ACanonicalFormError):
            await ingestor.ingest(_CARD_URL)

    assert server.requests == 2
