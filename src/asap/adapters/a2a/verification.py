"""Verify the signatures of an A2A v1.0 Agent Card (COMPAT-006).

Rules (``docs/guides/interoperability.md``, "Signed Agent Cards"):

- Every entry of ``signatures`` is read before any key lookup
  (:mod:`asap.adapters.a2a.signature_header`); a bad entry fails the card.
- An entry whose ``alg`` is off the list is skipped: the resolver is never
  asked. An entry whose ``kid`` the resolver does not trust is skipped.
- An entry from a trusted key fails the card if the key does not fit its
  ``alg`` or the signature does not verify, even when another entry verifies.
  ``a2a-sdk`` 1.3.1 is laxer: it stops at the first valid signature.
- The bytes checked are the canonical form of the parsed card, so a field
  outside the A2A v1.0 schema is never covered and never in the result.
- A signed card whose ASAP extension params hold a value that the canonical
  form drops or rounds fails: the params become manifest data, and a stripped
  ``enum: []`` would map as a looser schema. Other uncovered values are
  reported in ``uncovered``; the mapping carries none of them.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from asap.adapters.a2a.canonical import canonical_card_form
from asap.adapters.a2a.errors import (
    A2ANoTrustedSignatureError,
    A2ASignerScopeError,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    quoted_prefix,
)
from asap.adapters.a2a.extension import ASAP_EXTENSION_URI
from asap.adapters.a2a.models import AgentCard
from asap.adapters.a2a.signature_header import read_card_signatures
from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, CardKey, check_card_signature
from asap.adapters.a2a.trust_scope import AgentScope, scope_allows

PINNED_KEY_SOURCE: Final = "pinned"
"""The ``source`` of a key from the pinned trust store; any other source is a ``jku`` URL."""


@dataclass(frozen=True, slots=True)
class TrustedCardKey:
    """A key that a resolver trusts for a ``kid``, with where it came from and whom it may sign for.

    ``source`` is :data:`PINNED_KEY_SOURCE` or the ``jku`` URL of the JWKS that
    holds the key; ``agents`` is that source's scope. The key is left out of ``repr``.
    """

    key: CardKey = field(repr=False)
    source: str
    agents: AgentScope


CardKeyResolver = Callable[[str, str | None], Awaitable[TrustedCardKey | None]]
"""``(kid, jku) -> trusted key``: the key for *kid* with its source and scope, or ``None``."""


@dataclass(frozen=True, slots=True)
class VerifiedCardSigner:
    """One signature that verified: its key's ``source``, its ``kid``, and the source's scope.

    ``source`` is :data:`PINNED_KEY_SOURCE` or a ``jku`` URL, so two JWKS that
    both hold ``kid: "k1"`` give two different signers.
    """

    source: str
    kid: str
    agents: AgentScope

    def may_sign_for(self, agent_urn: str) -> bool:
        """Return True if this signer's source may sign a card for *agent_urn*."""
        return scope_allows(self.agents, agent_urn)


SignaturePolicy = Literal["require_signature", "allow_unsigned"]
"""A signature is required by default; ``allow_unsigned`` is an explicit opt-in."""

_POLICIES: tuple[SignaturePolicy, ...] = ("require_signature", "allow_unsigned")
_EXTENSION_PARAMS_PATH = re.compile(r"capabilities\.extensions\[(\d+)\]\.params")


@dataclass(frozen=True, slots=True)
class CardVerification:
    """Result of :func:`verify_agent_card`.

    ``verified`` is false only for an unsigned card accepted under
    ``allow_unsigned``: such a card is unverified data. ``signers`` lists each
    signature that verified, in card order. ``uncovered`` lists the values of a
    verified card that no signature covers, by path (see
    :func:`asap.adapters.a2a.canonical_card_form`); none of them is inside
    the ASAP extension params, and it is empty when ``verified`` is false,
    because then nothing is covered.
    """

    card: AgentCard
    verified: bool
    signers: tuple[VerifiedCardSigner, ...]
    uncovered: tuple[str, ...] = ()

    def require_signer_for(self, agent_urn: str) -> None:
        """Raise unless a signer that verified may sign a card for *agent_urn*.

        A verified result does not bind the card to an agent: call this with
        the agent you are about to trust (ingest calls it with the manifest
        ``id``). An unverified result (``allow_unsigned``) has no signer and
        binds nothing, so this returns: such a card stays unverified data.

        Raises:
            A2ASignerScopeError: No signer that verified may sign for *agent_urn*.
        """
        if not self.verified:
            return
        if not any(signer.may_sign_for(agent_urn) for signer in self.signers):
            raise A2ASignerScopeError(self.card.name, agent_urn, self.signers)


async def verify_agent_card(
    card: AgentCard,
    key_resolver: CardKeyResolver,
    *,
    algorithms: Collection[str] = CARD_VERIFY_ALGORITHMS,
    policy: SignaturePolicy = "require_signature",
) -> CardVerification:
    """Check the signatures of *card*; return the result or raise a typed error.

    An error raised by *key_resolver* propagates, so the card fails. Under
    ``allow_unsigned``, a card with signatures is still checked in full: a
    present, invalid signature never makes it "unsigned".

    Raises:
        A2AUnsignedCardError: No signature, and *policy* requires one.
        A2AInvalidSignatureError: A signature fails the card.
        A2ANoTrustedSignatureError: Every signature was skipped.
        A2AUncoveredExtensionError: The card verified, and its ASAP extension
            params hold a value that no signature covers.
        A2ACanonicalFormError: The card has no canonical form.
        ValueError: *algorithms* is not a non-empty subset of the default, or
            *policy* is not a :data:`SignaturePolicy`.

    Example:
        >>> result = await verify_agent_card(card, resolver)  # doctest: +SKIP
        >>> result.signers  # doctest: +SKIP
        (VerifiedCardSigner(source='pinned', kid='agent-key-1', agents=('urn:asap:agent:x',)),)
    """
    allowed = _checked_algorithms(algorithms)
    if policy not in _POLICIES:
        raise ValueError(f"policy must be one of {list(_POLICIES)}, got {policy!r}")
    if not card.signatures:
        if policy == "allow_unsigned":
            return CardVerification(card=card, verified=False, signers=())
        raise A2AUnsignedCardError(card.name)
    entries = read_card_signatures(card.signatures)
    form = canonical_card_form(card)
    payload = base64.urlsafe_b64encode(form.payload).rstrip(b"=").decode("ascii")
    signers: list[VerifiedCardSigner] = []
    skipped: list[str] = []
    for entry in entries:
        if entry.alg not in allowed:
            skipped.append(
                f"signature {entry.index}: alg {quoted_prefix(entry.alg)} is not allowed"
            )
            continue
        trusted = await key_resolver(entry.kid, entry.jku)
        if trusted is None:
            skipped.append(
                f"signature {entry.index}: kid {quoted_prefix(entry.kid)} is not trusted"
            )
            continue
        signing_input = f"{entry.protected}.{payload}".encode("ascii")
        check_card_signature(entry.index, entry.alg, trusted.key, signing_input, entry.signature)
        signers.append(VerifiedCardSigner(trusted.source, entry.kid, trusted.agents))
    if not signers:
        raise A2ANoTrustedSignatureError(card.name, skipped)
    _require_covered_asap_params(card, form.uncovered)
    return CardVerification(
        card=card, verified=True, signers=tuple(signers), uncovered=form.uncovered
    )


def _require_covered_asap_params(card: AgentCard, uncovered: Sequence[str]) -> None:
    """Refuse a signed card whose ASAP extension params hold a value no signature covers.

    One pass over the paths, with a set of ASAP extension indexes: a card that
    lists the extension many times must not cost paths times entries.
    """
    extensions = card.capabilities.extensions or []
    asap = {index for index, ext in enumerate(extensions) if ext.uri == ASAP_EXTENSION_URI}
    inside = [
        path
        for path in uncovered
        if (match := _EXTENSION_PARAMS_PATH.match(path)) and int(match.group(1)) in asap
    ]
    if inside:
        raise A2AUncoveredExtensionError(card.name, inside)


def _checked_algorithms(algorithms: Collection[str]) -> frozenset[str]:
    unknown = sorted(set(algorithms) - set(CARD_VERIFY_ALGORITHMS))
    if unknown or not algorithms:
        raise ValueError(
            f"algorithms must be a non-empty subset of {list(CARD_VERIFY_ALGORITHMS)}; "
            f"got {sorted(algorithms)}"
        )
    return frozenset(algorithms)
