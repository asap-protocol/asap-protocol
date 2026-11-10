"""Nonce replay must compare the full nonce string.

Prefix, case, and padding neighbors are different messages. Treating them as
the same nonce would drop legitimate traffic or miss a real replay.
"""

from __future__ import annotations

import pytest

from asap.errors import InvalidNonceError
from asap.models.envelope import Envelope
from asap.transport.validators import InMemoryNonceStore, validate_envelope_nonce

_EXACT_NONCE = "nonce-01"
_NEIGHBORS: tuple[str, ...] = (
    "nonce-010",
    "nonce-0",
    "Nonce-01",
    "nonce-01 ",
    " nonce-01",
)


def _envelope(nonce: str, sender: str = "urn:asap:agent:test") -> Envelope:
    """Build a request envelope whose only variables are the nonce and sender."""
    return Envelope(
        asap_version="0.1",
        sender=sender,
        recipient="urn:asap:agent:test",
        payload_type="TaskRequest",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        extensions={"nonce": nonce},
    )


def test_nonce_replay_key_match_is_exact() -> None:
    """Only the identical nonce is a replay, including reuse by another sender."""
    store = InMemoryNonceStore()
    validate_envelope_nonce(_envelope(_EXACT_NONCE), store)
    with pytest.raises(InvalidNonceError) as exc_info:
        validate_envelope_nonce(_envelope(_EXACT_NONCE), store)
    assert exc_info.value.nonce == _EXACT_NONCE
    for nonce in _NEIGHBORS:
        validate_envelope_nonce(_envelope(nonce), store)
    with pytest.raises(InvalidNonceError) as other_sender:
        validate_envelope_nonce(
            _envelope(_EXACT_NONCE, sender="urn:asap:agent:other"),
            store,
        )
    assert other_sender.value.nonce == _EXACT_NONCE
