"""Signing helpers for the A2A adapter tests: keys in ``a2a-sdk`` form, and other JWS names.

``sign_with_joserfc`` makes the signatures that ``sign_agent_card`` refuses to
make (the name ``Ed25519``, ``RS256``, or an unfit key), so tests can check what
a verifier does with them. ``forged_signature`` makes an entry with any header.
"""

from __future__ import annotations

import base64
import json
import warnings
from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from joserfc import jws
from joserfc.errors import SecurityWarning
from joserfc.jwk import Key

from asap.adapters.a2a import (
    ANY_AGENT,
    PINNED_KEY_SOURCE,
    AgentCard,
    AgentCardSignature,
    AgentScope,
    TrustedCardKey,
    canonicalize_agent_card,
)


def public_pem(private_key: PrivateKeyTypes) -> bytes:
    """The PEM public key, the form the ``a2a-sdk`` key provider returns."""
    return private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def private_pem(private_key: PrivateKeyTypes) -> bytes:
    """The PEM private key, the form the ``a2a-sdk`` signer takes."""
    return private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


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


def forged_signature(header: object, *, signature: str = "c2lnbmF0dXJl") -> AgentCardSignature:
    """A signature entry with *header* as its protected header (a ``str`` is raw JSON text)."""
    text = header if isinstance(header, str) else json.dumps(header)
    protected = base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()
    return AgentCardSignature(protected=protected, signature=signature)


class RecordingKeyResolver:
    """A card key resolver over fixed pinned keys that records every lookup.

    Each key is trusted for *agents* (any agent by default: these tests check
    signatures, not the binding of a card to its agent).
    """

    def __init__(self, keys: Mapping[str, Key], agents: AgentScope = ANY_AGENT) -> None:
        self.keys = dict(keys)
        self.agents = agents
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, kid: str, jku: str | None) -> TrustedCardKey | None:
        self.calls.append((kid, jku))
        key = self.keys.get(kid)
        return None if key is None else TrustedCardKey(key, PINNED_KEY_SOURCE, self.agents)
