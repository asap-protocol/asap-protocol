"""Deeply nested or oversized input fails as the module's typed errors.

``json.loads`` raises ``RecursionError``, not ``ValueError``, past about ten
thousand levels, and the pydantic serializer stops far earlier than the JSON
parser does. Neither escape may leave :mod:`asap.adapters.a2a` untyped.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    A2ACardFetchError,
    A2AInvalidSignatureError,
    A2AKeyResolutionError,
    A2AKeyResolver,
    A2AMappingError,
    A2ATrustConfig,
    AgentCard,
    AgentCardIngestor,
    agent_card_to_manifest,
    manifest_to_agent_card,
    sign_agent_card,
)
from asap.adapters.a2a import signature_header
from asap.adapters.a2a.signature_header import MAX_PROTECTED_HEADER_LENGTH, read_card_signatures
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import forged_signature

_KEY = ed25519.Ed25519PrivateKey.generate()
_JKU = "https://keys.example.com/jwks.json"
_CARD_URL = "https://agent.example.com/.well-known/agent-card.json"
# Deeper than json.loads accepts, and short enough for the 64 KiB JWKS limit.
_TOO_DEEP_FOR_JSON = 30_000


def _nested_array_text(depth: int) -> str:
    return "[" * depth + "]" * depth


def _deep_object(depth: int) -> dict[str, Any]:
    deep: dict[str, Any] = {"leaf": 1}
    for _ in range(depth):
        deep = {"next": deep}
    return deep


def _serving(body: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode()))


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _signed_card() -> AgentCard:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    return sign_agent_card(card, _KEY, kid="k1")


def test_a_nested_protected_header_is_a_malformed_entry() -> None:
    header = '{"alg":"EdDSA","kid":"k1","x":' + _nested_array_text(_TOO_DEEP_FOR_JSON) + "}"

    with pytest.raises(A2AInvalidSignatureError) as raised:
        read_card_signatures([forged_signature(header)])

    assert raised.value.reason == "malformed"


def test_an_oversized_protected_header_is_malformed_before_it_is_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signature = forged_signature({"alg": "EdDSA", "kid": "k" * MAX_PROTECTED_HEADER_LENGTH})

    def no_decoding(value: str) -> bytes:
        pytest.fail("a protected header longer than the bound was decoded")

    monkeypatch.setattr(signature_header, "_from_base64url", no_decoding)

    with pytest.raises(A2AInvalidSignatureError, match="longer than") as raised:
        read_card_signatures([signature])

    assert raised.value.reason == "malformed"


def test_a_recursion_error_while_decoding_a_header_is_a_malformed_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def too_deep(*args: object, **kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(signature_header.json, "loads", too_deep)

    with pytest.raises(A2AInvalidSignatureError) as raised:
        read_card_signatures([forged_signature({"alg": "EdDSA", "kid": "k1"})])

    assert raised.value.reason == "malformed"


async def test_a_nested_card_body_is_a_typed_fetch_error() -> None:
    ingestor = AgentCardIngestor(
        A2ATrustConfig(),
        dns_resolver=_public_dns,
        transport=_serving('{"name":' + _nested_array_text(_TOO_DEEP_FOR_JSON) + "}"),
    )

    with pytest.raises(A2ACardFetchError) as raised:
        await ingestor.ingest(_CARD_URL)

    assert raised.value.refusal == "invalid_json"


async def test_a_nested_jwks_is_a_typed_key_resolution_error() -> None:
    resolver = A2AKeyResolver(
        A2ATrustConfig(jku_allowlist=[_JKU]),
        dns_resolver=_public_dns,
        transport=_serving('{"keys":' + _nested_array_text(_TOO_DEEP_FOR_JSON) + "}"),
    )

    with pytest.raises(A2AKeyResolutionError) as raised:
        await resolver("k1", _JKU)

    assert raised.value.refusal == "invalid_json"


def test_mapping_a_card_with_deep_extension_params_is_a_typed_error() -> None:
    card_json = _signed_card().model_dump(mode="json")
    card_json["capabilities"]["extensions"].append({"uri": "urn:x", "params": _deep_object(2000)})

    with pytest.raises(A2AMappingError, match="nests deeper"):
        agent_card_to_manifest(AgentCard.model_validate(card_json))


async def test_a_deep_unprotected_header_on_a_valid_signature_is_a_typed_ingest_error() -> None:
    card_json = _signed_card().model_dump(mode="json")
    card_json["signatures"][0]["header"] = _deep_object(2000)
    public_jwk = {**OKPKey.import_key(_KEY.public_key()).as_dict(), "kid": "k1"}
    ingestor = AgentCardIngestor(
        A2ATrustConfig(pinned_keys={"k1": public_jwk}),
        dns_resolver=_public_dns,
        transport=_serving(json.dumps(card_json)),
    )

    with pytest.raises(A2AMappingError, match="nests deeper"):
        await ingestor.ingest(_CARD_URL)
