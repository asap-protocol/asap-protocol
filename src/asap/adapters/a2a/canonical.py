"""Canonical form of an A2A v1.0 Agent Card: the bytes that a card signature covers.

The form copies ``a2a-sdk`` 1.3.1 byte for byte
(``docs/guides/interoperability.md``, "Signed Agent Cards"). The model dump gives
rules 1, 3, and 4 (camelCase names; unset fields omitted; ``required`` and
``pkceRequired`` omitted when false). This module removes ``signatures``
(rule 2), drops ``""``, ``[]``, ``{}``, and ``null`` bottom-up, inside
``params`` too (rule 5), and applies RFC 8785 JCS (rule 6).

Rule 5 erases values, and JCS writes every number as a double, so the bytes can
cover less than the card holds. :func:`canonical_card_form` lists each such
value by path: the signer refuses such a card, and the verifier reports the paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import jcs
from pydantic_core import PydanticSerializationError

from asap.adapters.a2a.errors import A2ACanonicalFormError, describe_card, quoted_prefix
from asap.adapters.a2a.models import AgentCard

MAX_CARD_NESTING_DEPTH = 128
"""The most objects and arrays open on one path; the card object counts as 1.

The ``a2a-sdk`` limit (``a2a.utils._jcs.MAX_DEPTH``): a deeper card has no form there either.
"""

MAX_REPORTED_PATH_LENGTH = 256
"""Longest path that ``uncovered`` reports; a longer path is cut and ends with ``…``.

Without the cut, a chain of long keys makes every path below it nearly as long
as the card, and the work grows with the square of the card size. Every path
starts with its fixed head (for example
``capabilities.extensions[0].params``), so a prefix check still holds.
"""

_MAX_EXACT_INTEGER = 2**53 - 1
_MAX_REPORTED_PATH_LENGTH = 120
_REMOVED = object()


@dataclass(frozen=True, slots=True)
class CanonicalCardForm:
    """The signed form of a card.

    ``payload`` is the JCS bytes. ``uncovered`` lists the card values that the
    bytes do not cover exactly, by JSON path (``skills[0].examples``): each
    value that rule 5 removes (only the outermost removed path), and each
    integer beyond ±(2^53 − 1), which JCS writes as the nearest double.
    """

    payload: bytes
    uncovered: tuple[str, ...]


def canonical_card_form(card: AgentCard) -> CanonicalCardForm:
    """Return the canonical bytes of *card* and the values those bytes do not cover.

    Raises:
        A2ACanonicalFormError: The card nests deeper than
            :data:`MAX_CARD_NESTING_DEPTH`, or holds text that is not valid Unicode.

    Example:
        >>> canonical_card_form(card).uncovered  # doctest: +SKIP
        ('capabilities',)
    """
    try:
        dumped = card.model_dump(mode="json", exclude={"signatures"})
    except PydanticSerializationError as exc:
        raise A2ACanonicalFormError(
            f"{describe_card(card.name)} has nesting deeper than the model serializer allows"
        ) from exc
    uncovered: list[str] = []
    projection = _strip_empty_values(dumped, "", 1, uncovered)
    try:
        payload = cast(bytes, jcs.canonicalize(projection))
    except ValueError as exc:
        raise A2ACanonicalFormError(
            f"{describe_card(card.name)} holds text that is not valid Unicode: "
            f"{quoted_prefix(str(exc), _MAX_REPORTED_PATH_LENGTH)}"
        ) from exc
    return CanonicalCardForm(payload=payload, uncovered=tuple(uncovered))


def canonicalize_agent_card(card: AgentCard) -> bytes:
    """Return the RFC 8785 bytes that a signature over *card* covers.

    Example:
        >>> canonicalize_agent_card(card)  # doctest: +SKIP
        b'{"defaultInputModes":["text/plain"],...}'
    """
    return canonical_card_form(card).payload


def _strip_empty_values(value: Any, path: str, depth: int, uncovered: list[str]) -> Any:
    """Return *value* without empty values (rule 5), or ``_REMOVED`` if it is empty."""
    if isinstance(value, dict | list):
        if depth > MAX_CARD_NESTING_DEPTH:
            raise A2ACanonicalFormError(
                f"nesting deeper than {MAX_CARD_NESTING_DEPTH} (the a2a-sdk limit for a "
                f"canonical form) at {quoted_prefix(path, _MAX_REPORTED_PATH_LENGTH)}"
            )
        return _strip_container(value, path, depth, uncovered)
    if value is None or value == "":
        uncovered.append(path)
        return _REMOVED
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _MAX_EXACT_INTEGER:
        uncovered.append(path)
    return value


def _strip_container(
    value: dict[str, Any] | list[Any], path: str, depth: int, uncovered: list[str]
) -> Any:
    inner: list[str] = []
    if isinstance(value, dict):
        kept_container: dict[str, Any] | list[Any] = {
            key: kept
            for key, item in value.items()
            if (kept := _strip_empty_values(item, _member_path(path, key), depth + 1, inner))
            is not _REMOVED
        }
    else:
        kept_container = [
            kept
            for index, item in enumerate(value)
            if (
                kept := _strip_empty_values(item, _child_path(path, f"[{index}]"), depth + 1, inner)
            )
            is not _REMOVED
        ]
    if not kept_container:
        uncovered.append(path)
        return _REMOVED
    uncovered.extend(inner)
    return kept_container


def _member_path(path: str, key: str) -> str:
    return _child_path(path, f".{key}") if path else _child_path("", key)


def _child_path(path: str, part: str) -> str:
    """Append *part* to *path*, cut to :data:`MAX_REPORTED_PATH_LENGTH` plus ``…``."""
    if len(path) > MAX_REPORTED_PATH_LENGTH:
        return path  # already cut: every path below it reports as this one
    child = f"{path}{part}"
    return (
        child if len(child) <= MAX_REPORTED_PATH_LENGTH else child[:MAX_REPORTED_PATH_LENGTH] + "…"
    )
