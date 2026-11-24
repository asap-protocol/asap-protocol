"""Sign an A2A v1.0 Agent Card: one JWS with a detached payload per signature (A2A §8.4).

The payload is :func:`asap.adapters.a2a.canonicalize_agent_card`. The protected
header holds ``alg``, ``kid``, ``typ: "JOSE"``, and ``jku`` when given. An
Ed25519 key signs as ``EdDSA``, the only Ed25519 name that ``a2a-sdk`` 1.3.1
verifies (it rejects the RFC 9864 name ``Ed25519``); a P-256 key signs as
``ES256``. See ``docs/guides/interoperability.md``, "Signed Agent Cards".
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey, SECP256R1
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jws
from joserfc.errors import SecurityWarning
from joserfc.jwk import ECKey, OKPKey

from asap.adapters.a2a.canonical import canonical_card_form
from asap.adapters.a2a.errors import A2ACardNotSignableError, describe_card, describe_paths
from asap.adapters.a2a.models import AgentCard, AgentCardSignature

CardSigningAlgorithm = Literal["EdDSA", "ES256"]
CardSigningKey = Ed25519PrivateKey | EllipticCurvePrivateKey

_KEY_FOR_ALGORITHM = {"EdDSA": "an Ed25519 private key", "ES256": "a P-256 private key"}


def sign_agent_card(
    card: AgentCard,
    key: CardSigningKey,
    *,
    alg: CardSigningAlgorithm = "EdDSA",
    kid: str,
    jku: str | None = None,
) -> AgentCard:
    """Return a copy of *card* with one more signature, after any it already has.

    Raises:
        A2ACardNotSignableError: The canonical form would drop or round a value
            of the card (``paths`` names each one), *key* does not fit *alg*, or
            *kid* is empty.

    Example:
        >>> signed = sign_agent_card(card, agent_ed25519_key, kid="agent-key-1")  # doctest: +SKIP
        >>> signed = sign_agent_card(signed, p256_key, alg="ES256", kid="agent-ec-1")  # doctest: +SKIP
    """
    signing_key = _signing_jwk(key, alg)
    if not kid:
        raise A2ACardNotSignableError("kid must be a non-empty key id; got ''")
    form = canonical_card_form(card)
    if form.uncovered:
        raise A2ACardNotSignableError(
            f"{describe_card(card.name)} holds values that its canonical form drops or rounds, "
            f"so no signature would cover them: {describe_paths(form.uncovered)}. Remove "
            "each empty value (or give it content) and keep integers within ±(2^53 - 1)",
            paths=form.uncovered,
        )
    protected = {"alg": alg, "kid": kid, "typ": "JOSE"} | ({"jku": jku} if jku else {})
    with _without_eddsa_name_warning():
        compact = jws.serialize_compact(protected, form.payload, signing_key, algorithms=[alg])
    header, _, signature = jws.detach_content(compact).split(".")
    added = AgentCardSignature(protected=header, signature=signature)
    return card.model_copy(update={"signatures": [*(card.signatures or []), added]})


def _signing_jwk(key: CardSigningKey, alg: str) -> OKPKey | ECKey:
    if alg == "EdDSA" and isinstance(key, Ed25519PrivateKey):
        return OKPKey.import_key(key)
    if (
        alg == "ES256"
        and isinstance(key, EllipticCurvePrivateKey)
        and isinstance(key.curve, SECP256R1)
    ):
        return ECKey.import_key(key)
    if alg not in _KEY_FOR_ALGORITHM:
        raise A2ACardNotSignableError(
            f"alg {alg!r} is not a card signing algorithm; expected one of "
            f"{sorted(_KEY_FOR_ALGORITHM)} (verifiers also accept 'Ed25519' and 'RS256')"
        )
    raise A2ACardNotSignableError(
        f"alg {alg!r} needs {_KEY_FOR_ALGORITHM[alg]}, got a {type(key).__name__}"
    )


@contextmanager
def _without_eddsa_name_warning() -> Iterator[None]:
    # joserfc warns that RFC 9864 deprecates the name "EdDSA". ASAP emits it on
    # purpose (a2a-sdk 1.3.1 verifies no other Ed25519 name), so the warning is
    # silenced for this call only, never process-wide.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="EdDSA is deprecated", category=SecurityWarning)
        yield
