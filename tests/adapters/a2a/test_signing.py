"""Signing an A2A Agent Card: detached JWS over the canonical form.

Interop with the ``a2a-sdk`` verifier is in ``test_signing_interop.py``.
"""

from __future__ import annotations

import base64
import json
import warnings
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from joserfc import jws
from joserfc.errors import SecurityWarning
from joserfc.jwk import ECKey, OKPKey

from asap.adapters.a2a import (
    A2ACardNotSignableError,
    AgentCard,
    AgentCardSignature,
    canonicalize_agent_card,
    manifest_to_agent_card,
    sign_agent_card,
)
from asap.models.entities import Skill
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest

_ED25519_KEY = ed25519.Ed25519PrivateKey.generate()
_P256_KEY = ec.generate_private_key(ec.SECP256R1())


def _signable_card() -> AgentCard:
    return manifest_to_agent_card(research_manifest(), research_card_inputs()).card


def _protected_header(signature: AgentCardSignature) -> dict[str, Any]:
    padded = signature.protected + "=" * (-len(signature.protected) % 4)
    header: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return header


def _verify(card: AgentCard, signature: AgentCardSignature, public_key: Any, alg: str) -> None:
    jws.deserialize_compact(
        f"{signature.protected}..{signature.signature}",
        {"EdDSA": OKPKey, "ES256": ECKey}[alg].import_key(public_key),
        algorithms=[alg],
        payload=canonicalize_agent_card(card),
    )


@pytest.mark.parametrize(("alg", "key"), [("EdDSA", _ED25519_KEY), ("ES256", _P256_KEY)])
def test_signing_appends_a_detached_jws_over_the_canonical_form(alg: str, key: Any) -> None:
    card = _signable_card()

    signed = sign_agent_card(card, key, alg=alg, kid="card-key-1")

    assert card.signatures is None
    assert signed.signatures is not None and len(signed.signatures) == 1
    assert signed.model_copy(update={"signatures": None}) == card
    assert _protected_header(signed.signatures[0]) == {
        "alg": alg, "kid": "card-key-1", "typ": "JOSE"
    }  # fmt: skip
    _verify(signed, signed.signatures[0], key.public_key(), alg)


def test_the_default_algorithm_is_eddsa_and_jku_goes_into_the_protected_header() -> None:
    jku = "https://agent.example.com/.well-known/jwks.json"

    signed = sign_agent_card(_signable_card(), _ED25519_KEY, kid="k1", jku=jku)

    assert signed.signatures is not None
    assert _protected_header(signed.signatures[0]) == {
        "alg": "EdDSA", "kid": "k1", "typ": "JOSE", "jku": jku
    }  # fmt: skip


def test_a_card_can_carry_an_eddsa_and_an_es256_signature() -> None:
    card = _signable_card()

    signed = sign_agent_card(
        sign_agent_card(card, _ED25519_KEY, alg="EdDSA", kid="ed"), _P256_KEY, alg="ES256", kid="ec"
    )

    assert signed.signatures is not None
    assert [_protected_header(s)["kid"] for s in signed.signatures] == ["ed", "ec"]
    _verify(signed, signed.signatures[0], _ED25519_KEY.public_key(), "EdDSA")
    _verify(signed, signed.signatures[1], _P256_KEY.public_key(), "ES256")


def test_a_card_with_an_empty_required_message_is_not_signed() -> None:
    """The spec's example keeps an empty required field; the SDK form drops it (documented)."""
    card = AgentCard.model_validate(load_card_fixture("minimal_card.json"))

    with pytest.raises(A2ACardNotSignableError) as raised:
        sign_agent_card(card, _ED25519_KEY, kid="k1")

    assert raised.value.paths == ("capabilities",)
    assert raised.value.code == "asap:adapters/a2a/not_signable"


def test_a_card_whose_canonical_form_drops_values_is_not_signed() -> None:
    card = AgentCard.model_validate(load_card_fixture("edge_card.json"))

    with pytest.raises(A2ACardNotSignableError, match="documentationUrl") as raised:
        sign_agent_card(card, _ED25519_KEY, kid="k1")

    assert len(raised.value.paths) == 12


def test_a_skill_schema_with_an_empty_keyword_is_not_signed() -> None:
    schema = {"type": "object", "properties": {"mode": {"enum": []}}, "required": []}
    skill = Skill(id="web_research", description="Search", input_schema=schema)
    manifest = research_manifest(
        capabilities=research_manifest().capabilities.model_copy(update={"skills": [skill]})
    )
    inputs = research_card_inputs(skill_names={}, skill_tags={"web_research": ["research"]})
    card = manifest_to_agent_card(manifest, inputs).card

    with pytest.raises(A2ACardNotSignableError) as raised:
        sign_agent_card(card, _ED25519_KEY, kid="k1")

    schema_path = "capabilities.extensions[0].params.skill_schemas.web_research.input_schema"
    assert raised.value.paths == (f"{schema_path}.properties", f"{schema_path}.required")


def test_an_integer_with_no_exact_json_number_in_params_is_not_signed() -> None:
    data = load_card_fixture("minimal_card.json")
    data["capabilities"] = {"extensions": [{"uri": "urn:x", "params": {"n": 2**53}}]}

    with pytest.raises(A2ACardNotSignableError) as raised:
        sign_agent_card(AgentCard.model_validate(data), _ED25519_KEY, kid="k1")

    assert raised.value.paths == ("capabilities.extensions[0].params.n",)


@pytest.mark.parametrize(
    ("alg", "key"),
    [
        ("ES256", _ED25519_KEY),
        ("EdDSA", _P256_KEY),
        ("EdDSA", ed448.Ed448PrivateKey.generate()),
        ("ES256", ec.generate_private_key(ec.SECP384R1())),
        ("RS256", rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        ("Ed25519", _ED25519_KEY),
        ("HS256", _ED25519_KEY),
    ],
)
def test_a_key_that_does_not_fit_the_algorithm_is_refused(alg: str, key: Any) -> None:
    with pytest.raises(A2ACardNotSignableError, match="alg"):
        sign_agent_card(_signable_card(), key, alg=alg, kid="k1")


def test_an_empty_kid_is_refused() -> None:
    with pytest.raises(A2ACardNotSignableError, match="kid"):
        sign_agent_card(_signable_card(), _ED25519_KEY, kid="")


def test_eddsa_signing_emits_no_deprecation_warning_and_silences_nothing_else() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sign_agent_card(_signable_card(), _ED25519_KEY, kid="k1")
        jws.serialize_compact({"alg": "EdDSA"}, b"x", OKPKey.import_key(_ED25519_KEY), ["EdDSA"])

    # Only the direct joserfc call warns, after the signer ran: the silencing stays inside it.
    assert [w.category for w in caught] == [SecurityWarning]
