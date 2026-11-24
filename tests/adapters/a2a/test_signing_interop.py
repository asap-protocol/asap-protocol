"""Interop: a card signed by ``sign_agent_card`` verifies in the ``a2a-sdk`` 1.3.1 verifier.

This check guards the signing default: ``EdDSA`` must verify in the
SDK, or the default signing algorithm becomes ``ES256``. a2a-sdk is a dev
dependency only.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from a2a.utils.signing import InvalidSignaturesError, create_signature_verifier
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from google.protobuf.json_format import ParseDict
from joserfc.jwk import OKPKey

from asap.adapters.a2a import AgentCard, manifest_to_agent_card, sign_agent_card
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import public_pem, sign_with_joserfc

_ED25519_KEY = ed25519.Ed25519PrivateKey.generate()
_P256_KEY = ec.generate_private_key(ec.SECP256R1())
_JKU = "https://agent.example.com/.well-known/jwks.json"


def _generated_card() -> AgentCard:
    return manifest_to_agent_card(research_manifest(), research_card_inputs()).card


def _full_card() -> AgentCard:
    card = AgentCard.model_validate(load_card_fixture("full_card.json"))
    return card.model_copy(update={"signatures": None})


def _sdk_card(card: AgentCard) -> SdkAgentCard:
    return ParseDict(card.model_dump(mode="json"), SdkAgentCard(), ignore_unknown_fields=False)


def _sdk_verify(card: AgentCard, keys: dict[str, Any], algorithms: list[str]) -> None:
    def key_provider(kid: str | None, jku: str | None) -> bytes:
        return public_pem(keys[str(kid)])

    create_signature_verifier(key_provider, algorithms)(_sdk_card(card))


@pytest.mark.parametrize(("alg", "key"), [("EdDSA", _ED25519_KEY), ("ES256", _P256_KEY)])
@pytest.mark.parametrize("make_card", [_generated_card, _full_card])
def test_a_card_signed_here_verifies_in_the_sdk(alg: str, key: Any, make_card: Any) -> None:
    signed = sign_agent_card(make_card(), key, alg=alg, kid="k1", jku=_JKU)
    seen: list[tuple[str | None, str | None]] = []

    def key_provider(kid: str | None, jku: str | None) -> bytes:
        seen.append((kid, jku))
        return public_pem(key)

    create_signature_verifier(key_provider, [alg])(_sdk_card(signed))

    assert seen == [("k1", _JKU)]


def test_the_sdk_rejects_a_card_changed_after_signing() -> None:
    signed = sign_agent_card(_generated_card(), _ED25519_KEY, kid="k1")
    changed = signed.model_copy(update={"description": "Changed after signing"})

    with pytest.raises(InvalidSignaturesError):
        _sdk_verify(changed, {"k1": _ED25519_KEY}, ["EdDSA"])


def test_eddsa_plus_es256_passes_an_sdk_verifier_that_allows_only_es256_and_rs256() -> None:
    eddsa_only = sign_agent_card(_generated_card(), _ED25519_KEY, kid="ed")
    both = sign_agent_card(eddsa_only, _P256_KEY, alg="ES256", kid="ec")
    keys = {"ed": _ED25519_KEY, "ec": _P256_KEY}

    _sdk_verify(both, keys, ["ES256", "RS256"])
    with pytest.raises(InvalidSignaturesError):
        _sdk_verify(eddsa_only, keys, ["ES256", "RS256"])


@pytest.mark.parametrize("algorithms", [["Ed25519"], ["EdDSA"], ["Ed25519", "EdDSA"]])
def test_the_sdk_rejects_the_ed25519_name(algorithms: list[str]) -> None:
    """a2a-sdk 1.3.1 (PyJWT) has no ``Ed25519`` algorithm, so ASAP emits ``EdDSA``.

    If this test fails after an SDK upgrade, revisit which name ASAP emits.
    """
    card = sign_with_joserfc(
        _generated_card(), OKPKey.import_key(_ED25519_KEY), alg="Ed25519", kid="k1"
    )

    with pytest.raises(InvalidSignaturesError):
        _sdk_verify(card, {"k1": _ED25519_KEY}, algorithms)
