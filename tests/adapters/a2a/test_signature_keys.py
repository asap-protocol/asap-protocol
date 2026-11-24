"""Key and algorithm checks for Agent Card signatures.

The header ``alg`` picks a row of a fixed table; the key's own ``alg`` and
``use`` hints can only make a key unfit, never pick another algorithm.
"""

from __future__ import annotations

import base64
import warnings
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from joserfc import jws
from joserfc.errors import SecurityWarning
from joserfc.jwk import ECKey, Key, OctKey, OKPKey, RSAKey

from asap.adapters.a2a import A2AInvalidSignatureError
from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, check_card_signature

_ED25519 = OKPKey.import_key(ed25519.Ed25519PrivateKey.generate())
_ED448 = OKPKey.import_key(ed448.Ed448PrivateKey.generate())
_P256 = ECKey.import_key(ec.generate_private_key(ec.SECP256R1()))
_P384 = ECKey.import_key(ec.generate_private_key(ec.SECP384R1()))
_RSA = RSAKey.import_key(rsa.generate_private_key(public_exponent=65537, key_size=2048))
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SecurityWarning)  # joserfc warns on RSA below 2048 bits
    _RSA_1024 = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
    )
    _OCT = OctKey.import_key("a-shared-secret-of-enough-length")


def _public(key: Any, **members: Any) -> Key:
    """The public half of *key* as a JWK, with extra members such as ``use``."""
    public: Key = type(key).import_key({**key.as_dict(private=False), **members})
    return public


def _signed(alg: str, key: Key) -> tuple[bytes, bytes]:
    """The signing input and raw signature of a JWS over a fixed payload."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SecurityWarning)
        compact = jws.serialize_compact({"alg": alg, "kid": "k1"}, b"card", key, algorithms=[alg])
    head, payload, signature = compact.split(".")
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    return f"{head}.{payload}".encode(), raw


def test_the_verify_list_is_the_d3_list_with_both_ed25519_names() -> None:
    assert CARD_VERIFY_ALGORITHMS == ("ES256", "RS256", "EdDSA", "Ed25519")


@pytest.mark.parametrize(
    ("alg", "key"), [("EdDSA", _ED25519), ("Ed25519", _ED25519), ("ES256", _P256), ("RS256", _RSA)]
)
def test_each_allowed_algorithm_verifies_with_its_key_and_rejects_other_bytes(
    alg: str, key: Key
) -> None:
    signing_input, signature = _signed(alg, key)

    check_card_signature(0, alg, _public(key), signing_input, signature)
    with pytest.raises(A2AInvalidSignatureError) as raised:
        check_card_signature(2, alg, _public(key), signing_input + b"x", signature)

    assert (raised.value.reason, raised.value.index) == ("bad_signature", 2)


@pytest.mark.parametrize(("header_alg", "key_hint"), [("Ed25519", "EdDSA"), ("EdDSA", "Ed25519")])
def test_a_key_hint_with_the_other_ed25519_name_still_fits(header_alg: str, key_hint: str) -> None:
    signing_input, signature = _signed(header_alg, _ED25519)

    check_card_signature(0, header_alg, _public(_ED25519, alg=key_hint), signing_input, signature)


def test_eddsa_from_an_ed448_key_is_refused_although_joserfc_eddsa_accepts_it() -> None:
    signing_input, signature = _signed("EdDSA", _ED448)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SecurityWarning)
        legacy_eddsa = jws.JWSRegistry(algorithms=["EdDSA"]).get_alg("EdDSA")

    assert legacy_eddsa.verify(signing_input, signature, _public(_ED448))
    with pytest.raises(A2AInvalidSignatureError) as raised:
        check_card_signature(0, "EdDSA", _public(_ED448), signing_input, signature)

    assert raised.value.reason == "key_mismatch"


_UNFIT = {
    "Ed25519 name, P-256 key": ("Ed25519", _public(_P256)),
    "EdDSA, symmetric key": ("EdDSA", _OCT),
    "ES256, P-384 key": ("ES256", _public(_P384)),
    "ES256, Ed25519 key": ("ES256", _public(_ED25519)),
    "RS256, 1024-bit key": ("RS256", _RSA_1024),
    "RS256, P-256 key": ("RS256", _public(_P256)),
    "use enc": ("ES256", _public(_P256, use="enc")),
    "alg hint HS256": ("ES256", _public(_P256, alg="HS256")),
    "alg hint RS256": ("ES256", _public(_P256, alg="RS256")),
    "key_ops without verify": ("ES256", _public(_P256, key_ops=["sign"])),
}


@pytest.mark.parametrize(("alg", "key"), list(_UNFIT.values()), ids=list(_UNFIT))
def test_a_trusted_key_that_does_not_fit_the_alg_fails_before_any_signature_check(
    alg: str, key: Key
) -> None:
    signing_input, signature = _signed("ES256", _P256)

    with pytest.raises(A2AInvalidSignatureError, match="does not fit") as raised:
        check_card_signature(1, alg, key, signing_input, signature)

    assert (raised.value.reason, raised.value.index) == ("key_mismatch", 1)
    assert "a-shared-secret" not in f"{raised.value} {raised.value.details}"
