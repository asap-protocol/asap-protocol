"""Unit tests for ``asap.models.validators`` helpers."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from asap.discovery.registry import RegistryEntry
from asap.models.constants import MAX_URN_LENGTH
from asap.models.entities import Agent, Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.validators import validate_agent_urn, validate_percentage_format


def test_validate_percentage_format_accepts_common_forms() -> None:
    assert validate_percentage_format("99.5%") == "99.5%"
    assert validate_percentage_format(" 1% ") == "1%"
    assert validate_percentage_format("-0.5") == "-0.5"


def test_validate_percentage_format_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid percentage string"):
        validate_percentage_format("not a number")


def test_validate_agent_urn_rejects_excessive_length() -> None:
    prefix = "urn:asap:agent:"
    filler_len = MAX_URN_LENGTH - len(prefix) + 1
    too_long = prefix + ("x" * filler_len)
    assert len(too_long) > MAX_URN_LENGTH
    with pytest.raises(ValueError, match="at most"):
        validate_agent_urn(too_long)


def test_validate_agent_urn_rejects_bad_pattern() -> None:
    with pytest.raises(ValueError, match="Agent ID must follow URN format"):
        validate_agent_urn("https://example.invalid/agent")


@pytest.mark.parametrize("urn", ["urn:asap:agent:research", "urn:asap:agent:research:sub"])
def test_validate_agent_urn_rejects_trailing_newline(urn: str) -> None:
    """``$`` also matches before a final newline; the id must match in full."""
    assert validate_agent_urn(urn) == urn
    with pytest.raises(ValueError, match="Agent ID must follow URN format"):
        validate_agent_urn(urn + "\n")


_URN = "urn:asap:agent:research"
_ENVELOPE = {
    "asap_version": "0.1",
    "sender": _URN,
    "recipient": _URN,
    "payload_type": "task.request",
    "payload": {"conversation_id": "c", "skill_id": "s", "input": {}},
}


@pytest.mark.parametrize(
    "build",
    [
        lambda: Manifest(
            id=_URN + "\n",
            name="n",
            version="1.0.0",
            description="d",
            capabilities=Capability(asap_version="0.1", skills=[Skill(id="s", description="d")]),
            endpoints=Endpoint(asap="http://localhost:8000/asap"),
        ),
        lambda: Agent(id=_URN + "\n", manifest_uri="https://example.com/m", capabilities=["c"]),
        lambda: Envelope.model_validate({**_ENVELOPE, "sender": _URN + "\n"}),
        lambda: Envelope.model_validate({**_ENVELOPE, "recipient": _URN + "\n"}),
        lambda: RegistryEntry.model_validate(
            {"id": _URN + "\n", "name": "n", "description": "d", "asap_version": "0.1"}
            | {"endpoints": {"http": "https://example.com/asap"}}
        ),
    ],
    ids=["manifest-id", "agent-id", "envelope-sender", "envelope-recipient", "registry-entry-id"],
)
def test_models_reject_agent_urn_with_trailing_newline(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationError, match="Agent ID must follow URN format"):
        build()
