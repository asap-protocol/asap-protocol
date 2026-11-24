"""Tests for the A2A v1.0 AgentCard model and the rejection of v0.3 cards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import A2AAdapterError, A2AUnsupportedVersionError, AgentCard
from asap.errors import FatalError

_FIXTURES = Path(__file__).parent / "fixtures"
_REQUIRED_FIELDS = [
    "name",
    "description",
    "supportedInterfaces",
    "version",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
]


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return data


def test_minimal_card_parses_and_dumps_unchanged() -> None:
    data = _fixture("minimal_card.json")

    card = AgentCard.model_validate(data)

    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.model_dump(mode="json") == data


def test_card_with_every_optional_core_field_round_trips() -> None:
    data = {
        **_fixture("minimal_card.json"),
        "provider": {"url": "https://example.com", "organization": "Example Org"},
        "documentationUrl": "https://example.com/docs",
        "iconUrl": "https://example.com/icon.png",
        "capabilities": {
            "streaming": False,
            "pushNotifications": True,
            "extendedAgentCard": False,
            "extensions": [{"uri": "https://example.com/ext/v1", "params": {"k": "v"}}],
        },
        "signatures": [{"protected": "eyJhbGciOiJFZERTQSJ9", "signature": "c2ln"}],
    }

    assert AgentCard.model_validate(data).model_dump(mode="json") == data


def test_null_optional_fields_and_unknown_fields_are_dropped() -> None:
    data = _fixture("minimal_card.json")

    card = AgentCard.model_validate({**data, "provider": None, "iconUrl": None, "futureField": 1})

    assert card.model_dump(mode="json") == data


@pytest.mark.parametrize("field", _REQUIRED_FIELDS)
def test_card_rejects_a_missing_required_field(field: str) -> None:
    data = {k: v for k, v in _fixture("minimal_card.json").items() if k != field}

    with pytest.raises(ValidationError):
        AgentCard.model_validate(data)


@pytest.mark.parametrize(
    ("field", "empty"),
    [
        ("name", ""),
        ("description", ""),
        ("version", ""),
        ("supportedInterfaces", []),
        ("defaultInputModes", []),
        ("defaultOutputModes", []),
        ("skills", []),
    ],
)
def test_card_rejects_an_empty_required_field(field: str, empty: Any) -> None:
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**_fixture("minimal_card.json"), field: empty})


def test_v03_card_is_rejected_with_a_typed_error_that_names_the_version() -> None:
    raw = (_FIXTURES / "v03_card.json").read_text(encoding="utf-8")

    with pytest.raises(A2AUnsupportedVersionError) as from_dict:
        AgentCard.model_validate(json.loads(raw))
    with pytest.raises(A2AUnsupportedVersionError):
        AgentCard.model_validate_json(raw)

    error = from_dict.value
    assert isinstance(error, A2AAdapterError)
    assert isinstance(error, FatalError)
    assert error.found_version == "0.3.0"
    assert error.code == "asap:adapters/a2a/unsupported_version"
    assert "'0.3.0'" in error.message
    assert error.details == {"found_version": "0.3.0", "supported_version": "1.0"}


def test_card_with_top_level_url_and_no_version_is_reported_as_v03() -> None:
    data = {k: v for k, v in _fixture("v03_card.json").items() if k != "protocolVersion"}

    with pytest.raises(A2AUnsupportedVersionError) as excinfo:
        AgentCard.model_validate(data)

    assert excinfo.value.found_version == "0.3"


def test_v1_card_with_a_stray_top_level_url_parses_as_v1() -> None:
    data = _fixture("minimal_card.json")

    card = AgentCard.model_validate({**data, "url": "https://agent.example.com/a2a"})

    assert card.model_dump(mode="json") == data
