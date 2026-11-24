"""Untrusted text in A2A adapter errors is escaped and bounded.

A card name, version, URN, skill id, dict key, header value, URL, or validator
message comes from the card, its signatures, or a JWKS. In an error message
it is escaped (a newline cannot start a forged log line) and cut, so a
500 KB name never makes a 500 KB message. One case per error class.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    A2AAdapterError,
    A2ACardFetchError,
    A2AInvalidSignatureError,
    A2AKeyResolutionError,
    A2AKeyResolver,
    A2AMappingError,
    A2ANoTrustedSignatureError,
    A2ATrustConfig,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    A2AUnsupportedVersionError,
    AgentCard,
    AgentCardIngestor,
    agent_card_to_manifest,
    manifest_to_agent_card,
    verify_agent_card,
)
from asap.adapters.a2a.signature_header import read_card_signatures
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import RecordingKeyResolver, forged_signature

_MAX_MESSAGE_LENGTH = 4096
_EVIL = "a\nforged: log line\r\x00 " + "x" * 500_000
# Short enough for a protected header and a 64 KiB JWKS.
_EVIL_SHORT = "k\nforged: log line " + "y" * 2_000
_KEY = ed25519.Ed25519PrivateKey.generate()


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _card_json(**changes: Any) -> dict[str, Any]:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    return {**card.model_dump(mode="json"), **changes}


def _params(card_json: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = card_json["capabilities"]["extensions"][0]["params"]
    return params


def _card(**changes: Any) -> AgentCard:
    return AgentCard.model_validate(_card_json(**changes))


async def _unsupported_version() -> object:
    return AgentCard.model_validate({"url": "https://a.example", "protocolVersion": _EVIL})


async def _unsigned_card() -> object:
    return await verify_agent_card(_card(name=_EVIL), RecordingKeyResolver({}))


async def _no_trusted_signature() -> object:
    signature = forged_signature({"alg": "EdDSA", "kid": _EVIL_SHORT})
    return await verify_agent_card(
        _card(name=_EVIL, signatures=[signature]), RecordingKeyResolver({})
    )


async def _invalid_signature() -> object:
    return read_card_signatures([forged_signature({"alg": f"HS{_EVIL_SHORT}", "kid": "k1"})])


async def _key_resolution() -> object:
    member = {**OKPKey.import_key(_KEY.public_key()).as_dict(), "kid": _EVIL_SHORT}
    jku = "https://keys.example.com/jwks.json"
    jwks = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [member] * 2}))
    resolver = A2AKeyResolver(
        A2ATrustConfig(jku_allowlist=[jku]), dns_resolver=_public_dns, transport=jwks
    )
    return await resolver("k1", jku)


async def _card_fetch() -> object:
    server = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ingestor = AgentCardIngestor(A2ATrustConfig(), dns_resolver=_public_dns, transport=server)
    return await ingestor.ingest(f"https://agent.example.com/{_EVIL}")


async def _uncovered_extension() -> object:
    raise A2AUncoveredExtensionError(_EVIL, [f"capabilities.extensions[0].params.{_EVIL}"] * 50)


async def _mapping_validator_message() -> object:
    return agent_card_to_manifest(_card(version=_EVIL))


async def _mapping_dict_key() -> object:
    card_json = _card_json()
    _params(card_json)["skill_schemas"] = {_EVIL: {"input_schema": "not an object"}}
    return agent_card_to_manifest(AgentCard.model_validate(card_json))


_CASES: dict[str, tuple[type[A2AAdapterError], Callable[[], Awaitable[object]]]] = {
    "unsupported version": (A2AUnsupportedVersionError, _unsupported_version),
    "unsigned card": (A2AUnsignedCardError, _unsigned_card),
    "no trusted signature": (A2ANoTrustedSignatureError, _no_trusted_signature),
    "invalid signature": (A2AInvalidSignatureError, _invalid_signature),
    "key resolution": (A2AKeyResolutionError, _key_resolution),
    "card fetch": (A2ACardFetchError, _card_fetch),
    "uncovered extension": (A2AUncoveredExtensionError, _uncovered_extension),
    "mapping, validator message": (A2AMappingError, _mapping_validator_message),
    "mapping, dict key in loc": (A2AMappingError, _mapping_dict_key),
}


@pytest.mark.parametrize(("error", "case"), list(_CASES.values()), ids=list(_CASES))
async def test_untrusted_text_in_an_error_message_is_escaped_and_bounded(
    error: type[A2AAdapterError], case: Callable[[], Awaitable[object]]
) -> None:
    with pytest.raises(error) as raised:
        await case()

    message = str(raised.value)
    assert [char for char in message if not char.isprintable()] == []
    assert len(message) < _MAX_MESSAGE_LENGTH
    assert len(repr(raised.value.details)) < _MAX_MESSAGE_LENGTH
