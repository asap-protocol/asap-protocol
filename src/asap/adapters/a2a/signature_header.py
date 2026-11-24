"""Read the signature entries of an A2A Agent Card before any key lookup (COMPAT-006).

An entry passes when ``protected`` is a base64url JSON object with unique
member names, a string ``alg``, a non-empty string ``kid``, an optional string
``jku`` (``null`` reads as absent: ``a2a-sdk`` writes it so), and no ``crit``;
and ``signature`` is base64url. Any other entry fails the card, and so do
``alg: none`` and every ``HS*`` name: they never reach a key lookup. A card
carries at most :data:`MAX_CARD_SIGNATURES` entries.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from asap.adapters.a2a.errors import A2AInvalidSignatureError
from asap.adapters.a2a.models import AgentCardSignature

MAX_CARD_SIGNATURES = 16
"""Most signatures a card may carry: each one may cost a key lookup and a signature check."""

_BASE64URL = re.compile(r"[A-Za-z0-9_-]*")
_MAX_QUOTED_LENGTH = 64


@dataclass(frozen=True, slots=True)
class CardSignatureEntry:
    """One entry of ``signatures``, read: its position, header members, and raw signature.

    ``protected`` is the base64url text as the card holds it: the signing input
    starts with it.
    """

    index: int
    alg: str
    kid: str
    jku: str | None
    protected: str
    signature: bytes


def read_card_signatures(signatures: Sequence[AgentCardSignature]) -> list[CardSignatureEntry]:
    """Read every entry, in order; raise for the first one that fails the card.

    Raises:
        A2AInvalidSignatureError: ``malformed`` for a bad entry or too many
            entries, ``forbidden_algorithm`` for ``none`` or an HMAC name.
    """
    if len(signatures) > MAX_CARD_SIGNATURES:
        raise A2AInvalidSignatureError(
            MAX_CARD_SIGNATURES,
            "malformed",
            f"a card carries at most {MAX_CARD_SIGNATURES} signatures; "
            f"this one has {len(signatures)}",
        )
    return [_read_entry(index, entry) for index, entry in enumerate(signatures)]


def quoted_prefix(value: str) -> str:
    """Quote an untrusted header value for a message, cut to a bounded length."""
    return repr(value[:_MAX_QUOTED_LENGTH])


def _read_entry(index: int, entry: AgentCardSignature) -> CardSignatureEntry:
    header, signature = _decode_entry(index, entry)
    alg, kid, jku = header.get("alg"), header.get("kid"), header.get("jku")
    if (
        not isinstance(alg, str)
        or not isinstance(kid, str)
        or not kid
        or not isinstance(jku, str | None)
        or "crit" in header
    ):
        raise A2AInvalidSignatureError(
            index,
            "malformed",
            "the protected header needs a string alg, a non-empty string kid, "
            "and an optional string jku; crit is not supported",
        )
    if alg.lower() == "none" or alg.upper().startswith("HS"):
        raise A2AInvalidSignatureError(
            index, "forbidden_algorithm", f"alg {quoted_prefix(alg)} is never accepted"
        )
    return CardSignatureEntry(index, alg, kid, jku, entry.protected, signature)


def _decode_entry(index: int, entry: AgentCardSignature) -> tuple[dict[str, Any], bytes]:
    try:
        header_json = _from_base64url(entry.protected).decode("utf-8")
        header = json.loads(header_json, object_pairs_hook=_unique_members)
        if not isinstance(header, dict):
            raise ValueError("the protected header is not a JSON object")
        return header, _from_base64url(entry.signature)
    except ValueError as exc:
        raise A2AInvalidSignatureError(
            index,
            "malformed",
            "protected must be a base64url JSON object with unique member names, "
            "and signature must be base64url",
        ) from exc


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    members = dict(pairs)
    if len(members) != len(pairs):
        raise ValueError("a member name repeats")
    return members


def _from_base64url(value: str) -> bytes:
    if not _BASE64URL.fullmatch(value) or len(value) % 4 == 1:
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
