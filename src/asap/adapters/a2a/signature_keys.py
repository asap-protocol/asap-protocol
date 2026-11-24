"""Key and algorithm checks for A2A Agent Card signatures (COMPAT-006).

The ``alg`` of a signature picks one row of a fixed table; the key's own
``alg`` and ``use`` hints can only make it unfit, never pick another path:

- ``EdDSA`` and ``Ed25519`` (the RFC 9864 name): an Ed25519 key, never Ed448.
  Both verify with the ``joserfc`` ``Ed25519`` model, which raises no
  deprecation warning; the legacy ``EdDSA`` model would also accept Ed448.
- ``ES256``: a P-256 key. ``RS256``: an RSA key of at least 2048 bits.
- A key whose ``use`` is not ``sig``, whose ``alg`` hint names another
  algorithm, or whose ``key_ops`` exclude ``verify`` does not fit.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey, SECP256R1
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from joserfc.errors import JoseError
from joserfc.jwk import Key
from joserfc.jws import JWSRegistry

from asap.adapters.a2a.errors import A2AInvalidSignatureError

CardKey = Key
"""A ``joserfc`` JWK. The verifier uses only its public half."""

CARD_VERIFY_ALGORITHMS: tuple[str, ...] = ("ES256", "RS256", "EdDSA", "Ed25519")
"""The verify list. ``none`` and HMAC names are refused before any key lookup."""

MIN_RSA_KEY_BITS = 2048

_ALGORITHM_FAMILY = {"EdDSA": "Ed25519", "Ed25519": "Ed25519", "ES256": "ES256", "RS256": "RS256"}
_VERIFY_MODELS = {
    alg: JWSRegistry(algorithms=[family]).get_alg(family)
    for alg, family in _ALGORITHM_FAMILY.items()
}


def check_card_signature(
    index: int, alg: str, key: CardKey, signing_input: bytes, signature: bytes
) -> None:
    """Raise unless *signature* over *signing_input* verifies under *alg* with *key*.

    *alg* must be one of :data:`CARD_VERIFY_ALGORITHMS`; the caller checks that first.

    Raises:
        A2AInvalidSignatureError: ``key_mismatch`` if *key* does not fit *alg*,
            ``bad_signature`` if the signature does not verify.
    """
    if not _key_fits(alg, key):
        raise A2AInvalidSignatureError(
            index, "key_mismatch", f"the trusted key for this kid does not fit alg {alg!r}"
        )
    if not _VERIFY_MODELS[alg].verify(signing_input, signature, key):
        raise A2AInvalidSignatureError(
            index, "bad_signature", "the signature does not match the card's canonical form"
        )


def _key_fits(alg: str, key: CardKey) -> bool:
    hint = key.get("alg")
    if key.get("use", "sig") != "sig":
        return False
    if hint is not None and _ALGORITHM_FAMILY.get(str(hint)) != _ALGORITHM_FAMILY[alg]:
        return False
    try:
        public = key.get_op_key("verify")
    except JoseError:
        return False
    if alg == "ES256":
        return isinstance(public, EllipticCurvePublicKey) and isinstance(public.curve, SECP256R1)
    if alg == "RS256":
        return isinstance(public, RSAPublicKey) and public.key_size >= MIN_RSA_KEY_BITS
    return isinstance(public, Ed25519PublicKey)
