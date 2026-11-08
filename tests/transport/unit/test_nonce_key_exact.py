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


def _envelope(nonce: str) -> Envelope:
    """Build a request envelope whose only variable is the nonce."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:test",
        recipient="urn:asap:agent:test",
        payload_type="TaskRequest",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        extensions={"nonce": nonce},
    )


def test_nonce_replay_key_match_is_exact() -> None:
    """Only the identical nonce is a replay; neighbors stay first use."""
    store = InMemoryNonceStore()
    validate_envelope_nonce(_envelope(_EXACT_NONCE), store)
    with pytest.raises(InvalidNonceError) as exc_info:
        validate_envelope_nonce(_envelope(_EXACT_NONCE), store)
    assert exc_info.value.nonce == _EXACT_NONCE
    for nonce in _NEIGHBORS:
        validate_envelope_nonce(_envelope(nonce), store)
