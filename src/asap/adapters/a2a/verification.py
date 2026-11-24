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
from dataclasses import dataclass
from typing import Literal

from asap.adapters.a2a.canonical import canonical_card_form
from asap.adapters.a2a.errors import (
    A2ANoTrustedSignatureError,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    quoted_prefix,
)
from asap.adapters.a2a.extension import ASAP_EXTENSION_URI
from asap.adapters.a2a.models import AgentCard
from asap.adapters.a2a.signature_header import read_card_signatures
from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, CardKey, check_card_signature

CardKeyResolver = Callable[[str, str | None], Awaitable[CardKey | None]]
"""``(kid, jku) -> key``: a trusted public key for *kid*, or ``None`` if it is not trusted."""

SignaturePolicy = Literal["require_signature", "allow_unsigned"]
"""A signature is required by default; ``allow_unsigned`` is an explicit opt-in."""

_POLICIES: tuple[SignaturePolicy, ...] = ("require_signature", "allow_unsigned")
_EXTENSION_PARAMS_PATH = re.compile(r"capabilities\.extensions\[(\d+)\]\.params")


@dataclass(frozen=True, slots=True)
class CardVerification:
    """Result of :func:`verify_agent_card`.

    ``verified`` is false only for an unsigned card accepted under
    ``allow_unsigned``: such a card is unverified data. ``signer_kids`` lists
    the keys whose signature verified. ``uncovered`` lists the values of a
    verified card that no signature covers, by path (see
    :func:`asap.adapters.a2a.canonical_card_form`); none of them is inside
    the ASAP extension params, and it is empty when ``verified`` is false,
    because then nothing is covered.
    """

    card: AgentCard
    verified: bool
    signer_kids: tuple[str, ...]
    uncovered: tuple[str, ...] = ()


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
        >>> result.signer_kids  # doctest: +SKIP
        ('agent-key-1',)
    """
    allowed = _checked_algorithms(algorithms)
    if policy not in _POLICIES:
        raise ValueError(f"policy must be one of {list(_POLICIES)}, got {policy!r}")
    if not card.signatures:
        if policy == "allow_unsigned":
            return CardVerification(card=card, verified=False, signer_kids=())
        raise A2AUnsignedCardError(card.name)
    entries = read_card_signatures(card.signatures)
    form = canonical_card_form(card)
    payload = base64.urlsafe_b64encode(form.payload).rstrip(b"=").decode("ascii")
    signer_kids: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        if entry.alg not in allowed:
            skipped.append(
                f"signature {entry.index}: alg {quoted_prefix(entry.alg)} is not allowed"
            )
            continue
        key = await key_resolver(entry.kid, entry.jku)
        if key is None:
            skipped.append(
                f"signature {entry.index}: kid {quoted_prefix(entry.kid)} is not trusted"
            )
            continue
        signing_input = f"{entry.protected}.{payload}".encode("ascii")
        check_card_signature(entry.index, entry.alg, key, signing_input, entry.signature)
        signer_kids.append(entry.kid)
    if not signer_kids:
        raise A2ANoTrustedSignatureError(card.name, skipped)
    _require_covered_asap_params(card, form.uncovered)
    return CardVerification(
        card=card, verified=True, signer_kids=tuple(signer_kids), uncovered=form.uncovered
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
