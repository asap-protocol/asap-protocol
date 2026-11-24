"""Reading the signature entries of a card before any key lookup.

A malformed entry, ``alg: none``, an HMAC name, or too many entries fail the
card here, so no key is ever looked up for them.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from asap.adapters.a2a import (
    MAX_CARD_SIGNATURES,
    A2AInvalidSignatureError,
    AgentCardSignature,
    manifest_to_agent_card,
    sign_agent_card,
)
from asap.adapters.a2a.signature_header import read_card_signatures
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import forged_signature

_JKU = "https://agent.example.com/.well-known/jwks.json"


def _valid_signature() -> AgentCardSignature:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    signed = sign_agent_card(card, ed25519.Ed25519PrivateKey.generate(), kid="k1", jku=_JKU)
    assert signed.signatures is not None
    return signed.signatures[0]


def test_an_entry_made_by_sign_agent_card_reads_back() -> None:
    signature = _valid_signature()

    [entry] = read_card_signatures([signature])

    assert (entry.index, entry.alg, entry.kid, entry.jku) == (0, "EdDSA", "k1", _JKU)
    assert (entry.protected, len(entry.signature)) == (signature.protected, 64)


def test_a_null_jku_reads_as_absent_as_the_sdk_writes_it() -> None:
    [entry] = read_card_signatures([forged_signature({"alg": "ES256", "kid": "k", "jku": None})])

    assert entry.jku is None


@pytest.mark.parametrize("alg", ["none", "None", "HS256", "HS512"])
def test_none_and_hmac_names_fail_the_card(alg: str) -> None:
    signatures = [_valid_signature(), forged_signature({"alg": alg, "kid": "k1"})]

    with pytest.raises(A2AInvalidSignatureError, match="never accepted") as raised:
        read_card_signatures(signatures)

    assert (raised.value.reason, raised.value.index) == ("forbidden_algorithm", 1)


_MALFORMED = {
    "protected not base64url": AgentCardSignature(protected="e30*", signature="c2ln"),
    "protected not UTF-8": AgentCardSignature(protected="_w", signature="c2ln"),
    "protected not JSON": forged_signature("not json"),
    "protected not an object": forged_signature(["EdDSA", "k1"]),
    "no kid": forged_signature({"alg": "EdDSA"}),
    "empty kid": forged_signature({"alg": "EdDSA", "kid": ""}),
    "alg not a string": forged_signature({"alg": 1, "kid": "k1"}),
    "jku not a string": forged_signature({"alg": "EdDSA", "kid": "k1", "jku": 1}),
    "crit member": forged_signature({"alg": "EdDSA", "kid": "k1", "crit": ["exp"], "exp": 1}),
    "repeated member": forged_signature('{"alg":"EdDSA","kid":"k1","alg":"EdDSA"}'),
    "signature not base64url": forged_signature({"alg": "EdDSA", "kid": "k1"}, signature="a+b/"),
    "signature of impossible length": forged_signature(
        {"alg": "EdDSA", "kid": "k1"}, signature="a"
    ),
}


@pytest.mark.parametrize("signature", list(_MALFORMED.values()), ids=list(_MALFORMED))
def test_a_malformed_entry_fails_the_card(signature: AgentCardSignature) -> None:
    with pytest.raises(A2AInvalidSignatureError) as raised:
        read_card_signatures([_valid_signature(), signature])

    assert (raised.value.reason, raised.value.index) == ("malformed", 1)
    assert raised.value.code == "asap:adapters/a2a/invalid_signature"


def test_more_entries_than_the_limit_fail_the_card() -> None:
    signatures = [_valid_signature()] * (MAX_CARD_SIGNATURES + 1)

    with pytest.raises(A2AInvalidSignatureError, match=f"at most {MAX_CARD_SIGNATURES}") as raised:
        read_card_signatures(signatures)

    assert (raised.value.reason, raised.value.index) == ("malformed", MAX_CARD_SIGNATURES)
    assert len(read_card_signatures(signatures[:MAX_CARD_SIGNATURES])) == MAX_CARD_SIGNATURES
