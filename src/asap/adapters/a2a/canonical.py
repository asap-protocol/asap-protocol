"""Canonical form of an A2A v1.0 Agent Card: the bytes that a card signature covers.

The form copies ``a2a-sdk`` 1.3.1 byte for byte
(``docs/guides/interoperability.md``, "Signed Agent Cards"). The model dump gives
rules 1, 3, and 4 (camelCase names; unset fields omitted; ``required`` and
``pkceRequired`` omitted when false). This module removes ``signatures``
(rule 2), drops ``""``, ``[]``, ``{}``, and ``null`` bottom-up, inside
``params`` too (rule 5), and applies RFC 8785 JCS (rule 6).
"""

from __future__ import annotations

from typing import Any, cast

import jcs

from asap.adapters.a2a.models import AgentCard

_REMOVED = object()


def canonicalize_agent_card(card: AgentCard) -> bytes:
    """Return the RFC 8785 bytes that a signature over *card* covers.

    Example:
        >>> canonicalize_agent_card(card)  # doctest: +SKIP
        b'{"defaultInputModes":["text/plain"],...}'
    """
    dumped = card.model_dump(mode="json", exclude={"signatures"})
    return cast(bytes, jcs.canonicalize(_strip_empty_values(dumped)))


def _strip_empty_values(value: Any) -> Any:
    """Return *value* without empty values (rule 5), or ``_REMOVED`` if it is empty."""
    if isinstance(value, dict):
        stripped: dict[str, Any] | list[Any] = {
            key: kept
            for key, item in value.items()
            if (kept := _strip_empty_values(item)) is not _REMOVED
        }
    elif isinstance(value, list):
        stripped = [kept for item in value if (kept := _strip_empty_values(item)) is not _REMOVED]
    else:
        return _REMOVED if value is None or value == "" else value
    return stripped or _REMOVED
