"""Signing helpers for the A2A adapter tests: keys in ``a2a-sdk`` form, and other JWS names.

``sign_with_joserfc`` makes the signatures that ``sign_agent_card`` refuses to
make (the name ``Ed25519``, ``RS256``, or an unfit key), so tests can check what
a verifier does with them.
"""

from __future__ import annotations

import warnings

from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from joserfc import jws
from joserfc.errors import SecurityWarning
from joserfc.jwk import Key

from asap.adapters.a2a import AgentCard, AgentCardSignature, canonicalize_agent_card


def public_pem(private_key: PrivateKeyTypes) -> bytes:
    """The PEM public key, the form the ``a2a-sdk`` key provider returns."""
    return private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def sign_with_joserfc(card: AgentCard, key: Key, *, alg: str, kid: str) -> AgentCard:
    """Append a detached JWS made by ``joserfc`` under any algorithm name it knows."""
    protected = {"alg": alg, "kid": kid, "typ": "JOSE"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SecurityWarning)
        compact = jws.serialize_compact(
            protected, canonicalize_agent_card(card), key, algorithms=[alg]
        )
    header, _, signature = jws.detach_content(compact).split(".")
    added = AgentCardSignature(protected=header, signature=signature)
    return card.model_copy(update={"signatures": [*(card.signatures or []), added]})
