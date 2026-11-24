"""Tests for the ASAP extension that carries manifest data inside an A2A Agent Card."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import (
    ASAP_EXTENSION_URI,
    A2AMappingError,
    AgentCard,
    AsapExtensionParams,
    read_asap_extension,
)
from asap.adapters.a2a.extension import build_asap_extension
from tests.adapters.a2a.helpers import load_card_fixture

_REQUIRED_PARAMS: dict[str, Any] = {
    "agent_urn": "urn:asap:agent:research",
    "asap_endpoint": "https://agent.example.com/asap",
    "asap_version": "0.1",
    "state_persistence": False,
}


def test_extension_uri_is_the_maintainer_decision() -> None:
    assert ASAP_EXTENSION_URI == "https://asap-protocol.com/a2a/extensions/manifest/v1"


def test_extension_entry_is_optional_and_carries_snake_case_params() -> None:
    extension = build_asap_extension(AsapExtensionParams.model_validate(_REQUIRED_PARAMS))

    dumped = extension.model_dump(mode="json")

    assert dumped["uri"] == ASAP_EXTENSION_URI
    assert dumped["description"]
    assert "required" not in dumped
    assert dumped["params"] == _REQUIRED_PARAMS


def test_params_with_every_field_round_trip() -> None:
    params = {
        **_REQUIRED_PARAMS,
        "events_endpoint": "wss://agent.example.com/asap/events",
        "state_persistence": True,
        "skill_schemas": {
            "web_research": {"input_schema": {"type": "object"}},
            "summarize": {"output_schema": {"type": "string"}},
        },
    }

    extension = build_asap_extension(AsapExtensionParams.model_validate(params))

    assert extension.params == params


def test_unknown_params_are_ignored_on_read() -> None:
    params = AsapExtensionParams.model_validate({**_REQUIRED_PARAMS, "added_in_a_later_version": 1})

    assert build_asap_extension(params).params == _REQUIRED_PARAMS


@pytest.mark.parametrize("name", ["agent_urn", "asap_endpoint", "asap_version"])
def test_params_need_their_required_values(name: str) -> None:
    with pytest.raises(ValidationError):
        AsapExtensionParams.model_validate({**_REQUIRED_PARAMS, name: ""})
    with pytest.raises(ValidationError):
        AsapExtensionParams.model_validate({k: v for k, v in _REQUIRED_PARAMS.items() if k != name})


def test_state_persistence_defaults_to_false_and_rejects_non_booleans() -> None:
    without = {k: v for k, v in _REQUIRED_PARAMS.items() if k != "state_persistence"}

    assert AsapExtensionParams.model_validate(without).state_persistence is False
    with pytest.raises(ValidationError):
        AsapExtensionParams.model_validate({**_REQUIRED_PARAMS, "state_persistence": "yes"})


def _card_with_extensions(extensions: list[dict[str, Any]]) -> AgentCard:
    data = load_card_fixture("minimal_card.json")
    return AgentCard.model_validate({**data, "capabilities": {"extensions": extensions}})


def test_read_returns_the_params_of_the_asap_extension() -> None:
    other = {"uri": "https://example.com/a2a/extensions/trace/v1"}
    asap = {"uri": ASAP_EXTENSION_URI, "params": _REQUIRED_PARAMS}

    params = read_asap_extension(_card_with_extensions([other, asap]))

    assert params == AsapExtensionParams.model_validate(_REQUIRED_PARAMS)
    assert read_asap_extension(_card_with_extensions([other])) is None


@pytest.mark.parametrize(
    ("extensions", "fields"),
    [
        (
            [{"uri": ASAP_EXTENSION_URI, "params": {"agent_urn": "urn:asap:agent:research"}}],
            ("asap_endpoint", "asap_version"),
        ),
        (
            [{"uri": ASAP_EXTENSION_URI, "params": _REQUIRED_PARAMS}] * 2,
            ("capabilities.extensions",),
        ),
    ],
    ids=["params-missing-values", "listed-twice"],
)
def test_read_raises_a_typed_error_for_a_broken_asap_extension(
    extensions: list[dict[str, Any]], fields: tuple[str, ...]
) -> None:
    with pytest.raises(A2AMappingError, match=ASAP_EXTENSION_URI) as excinfo:
        read_asap_extension(_card_with_extensions(extensions))

    assert excinfo.value.fields == fields
    assert excinfo.value.code == "asap:adapters/a2a/mapping"
