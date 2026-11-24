"""Client side of the A2A flow: an ``a2a-sdk`` client that trusts one pinned card key.

The card verifier never follows a ``jku``: a pinned ``kid`` is the only key
it trusts, and a signature under any other ``kid`` is skipped, so a card
signed only by an unknown key fails with ``InvalidSignaturesError``.
"""

from __future__ import annotations

from collections.abc import Callable

import jwt
from a2a.types import AgentCard
from a2a.utils.signing import create_signature_verifier
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def pinned_card_verifier(kid: str, public_key: Ed25519PublicKey) -> Callable[[AgentCard], None]:
    """Return an ``a2a-sdk`` card verifier that accepts only an ``EdDSA`` signature by *kid*.

    Example:
        >>> verify = pinned_card_verifier("gateway-card-1", card_public_key)  # doctest: +SKIP
        >>> await A2ACardResolver(http, origin).get_agent_card(signature_verifier=verify)
    """
    pem = public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    def key_provider(card_kid: str | None, jku: str | None) -> bytes:
        # The jku is ignored on purpose: following a card's own key URL would let
        # the card choose the key that verifies it.
        if card_kid != kid:
            raise jwt.InvalidKeyError("the signature's kid is not pinned")
        return pem

    return create_signature_verifier(key_provider, ["EdDSA"])
