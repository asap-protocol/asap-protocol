"""Verification interop and edge cases: cards signed by ``a2a-sdk`` 1.3.1.

Also pins key rotation, which is stricter here than in the SDK verifier, and
what a signature does not cover (``docs/guides/interoperability.md``, "Signed Agent Cards").
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from a2a.utils.signing import create_agent_card_signer, create_signature_verifier
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from google.protobuf.json_format import MessageToDict, ParseDict
from joserfc.jwk import ECKey, OKPKey, RSAKey
from pydantic import ValidationError

from asap.adapters.a2a import (
    A2AInvalidSignatureError,
    AgentCard,
    manifest_to_agent_card,
    sign_agent_card,
    verify_agent_card,
)
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import (
    RecordingKeyResolver,
    private_pem,
    public_pem,
    sign_with_joserfc,
)

_ED25519 = ed25519.Ed25519PrivateKey.generate()
_P256 = ec.generate_private_key(ec.SECP256R1())
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_JWKS = {
    "ed": OKPKey.import_key(_ED25519.public_key()),
    "ec": ECKey.import_key(_P256.public_key()),
    "rsa": RSAKey.import_key(_RSA.public_key()),
}


def _full_card_json() -> dict[str, Any]:
    card_json = load_card_fixture("full_card.json")
    card_json.pop("signatures")
    return card_json


def _sdk_signed(card_json: dict[str, Any], key: Any, alg: str, kid: str) -> dict[str, Any]:
    header = {"alg": alg, "kid": kid, "jku": None, "typ": "JOSE"}
    signer = create_agent_card_signer(private_pem(key), header)
    signed: dict[str, Any] = MessageToDict(signer(ParseDict(card_json, SdkAgentCard())))
    return signed


@pytest.mark.parametrize(
    ("alg", "key", "kid"),
    [("ES256", _P256, "ec"), ("RS256", _RSA, "rsa"), ("EdDSA", _ED25519, "ed")],
)
async def test_a_card_signed_by_the_sdk_verifies_here(alg: str, key: Any, kid: str) -> None:
    card = AgentCard.model_validate(_sdk_signed(_full_card_json(), key, alg, kid))
    resolver = RecordingKeyResolver(_PUBLIC_JWKS)

    result = await verify_agent_card(card, resolver)

    assert (result.verified, result.uncovered) == (True, ())
    assert [signer.kid for signer in result.signers] == [kid]
    assert resolver.calls == [(kid, None)]


async def test_a_field_outside_the_schema_is_not_covered_and_not_in_the_result() -> None:
    signed_json = _sdk_signed(_full_card_json(), _ED25519, "EdDSA", "ed")
    signed_json["unsignedNote"] = {"added": "after signing"}
    signed_json["skills"][0]["extraField"] = "added after signing"

    result = await verify_agent_card(
        AgentCard.model_validate(signed_json), RecordingKeyResolver(_PUBLIC_JWKS)
    )

    dumped = result.card.model_dump(mode="json")
    assert result.verified
    assert "unsignedNote" not in dumped and "extraField" not in dumped["skills"][0]


async def test_values_that_no_signature_covers_still_verify_and_are_reported() -> None:
    signed_json = _sdk_signed(_full_card_json(), _ED25519, "EdDSA", "ed")
    signed_json["supportedInterfaces"][0]["tenant"] = ""
    signed_json["securitySchemes"]["mtlsAdded"] = {"mtlsSecurityScheme": {}}
    signed_json["securityRequirements"].append({"schemes": {"bearer": {}}})

    result = await verify_agent_card(
        AgentCard.model_validate(signed_json), RecordingKeyResolver(_PUBLIC_JWKS)
    )

    assert result.verified
    assert result.uncovered == (
        "supportedInterfaces[0].tenant",
        "securitySchemes.mtlsAdded",
        "securityRequirements[2]",
    )


def test_a_card_with_an_empty_required_field_signed_by_the_sdk_fails_at_parse() -> None:
    signed_json = _sdk_signed({**_full_card_json(), "description": ""}, _ED25519, "EdDSA", "ed")

    assert "description" not in signed_json
    with pytest.raises(ValidationError, match="description"):
        AgentCard.model_validate(signed_json)


def _generated_card() -> AgentCard:
    return manifest_to_agent_card(research_manifest(), research_card_inputs()).card


_SIGNERS: dict[str, Callable[[AgentCard], AgentCard]] = {
    "ES256": lambda card: sign_agent_card(card, _P256, alg="ES256", kid="ec"),
    "EdDSA": lambda card: sign_agent_card(card, _ED25519, alg="EdDSA", kid="ed"),
    "Ed25519": lambda card: sign_with_joserfc(
        card, OKPKey.import_key(_ED25519), alg="Ed25519", kid="ed"
    ),
    "RS256": lambda card: sign_with_joserfc(card, RSAKey.import_key(_RSA), alg="RS256", kid="rsa"),
}


@pytest.mark.parametrize("alg", list(_SIGNERS))
async def test_each_allowed_algorithm_verifies_end_to_end(alg: str) -> None:
    card = _SIGNERS[alg](_generated_card())

    result = await verify_agent_card(card, RecordingKeyResolver(_PUBLIC_JWKS), algorithms=[alg])

    assert result.verified


async def test_rotation_a_valid_trusted_signature_and_an_unknown_kid_pass() -> None:
    card = sign_agent_card(
        sign_agent_card(_generated_card(), _ED25519, kid="retired"), _ED25519, kid="ed"
    )
    resolver = RecordingKeyResolver(_PUBLIC_JWKS)

    result = await verify_agent_card(card, resolver)

    assert [signer.kid for signer in result.signers] == ["ed"]
    assert resolver.calls == [("retired", None), ("ed", None)]


async def test_rotation_an_invalid_signature_from_a_trusted_key_fails_where_the_sdk_passes() -> (
    None
):
    """Stricter than a2a-sdk 1.3.1: the SDK stops at the first valid signature."""
    valid_first = sign_agent_card(_generated_card(), _ED25519, kid="ed")
    card = sign_agent_card(
        valid_first, ec.generate_private_key(ec.SECP256R1()), alg="ES256", kid="ec"
    )

    with pytest.raises(A2AInvalidSignatureError) as raised:
        await verify_agent_card(card, RecordingKeyResolver(_PUBLIC_JWKS))

    assert (raised.value.reason, raised.value.index) == ("bad_signature", 1)
    pems = {"ed": public_pem(_ED25519), "ec": public_pem(_P256)}
    sdk_verifier = create_signature_verifier(lambda kid, jku: pems[str(kid)], ["EdDSA", "ES256"])
    sdk_verifier(ParseDict(card.model_dump(mode="json"), SdkAgentCard()))
