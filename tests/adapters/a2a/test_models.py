"""Tests for the A2A v1.0 Agent Card part models (interfaces, skills, capabilities)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import (
    AgentCapabilities,
    AgentCardSignature,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

_INTERFACE: dict[str, Any] = {
    "url": "https://agent.example.com/a2a",
    "protocolBinding": "JSONRPC",
    "protocolVersion": "1.0",
}
_SKILL: dict[str, Any] = {
    "id": "web_research",
    "name": "Web research",
    "description": "Search the web and summarize the results",
    "tags": ["research"],
}


@pytest.mark.parametrize("binding_key", ["protocolBinding", "protocol_binding"])
def test_interface_reads_both_spellings_and_dumps_camel_case(binding_key: str) -> None:
    data = {"url": _INTERFACE["url"], "protocolVersion": "1.0", binding_key: "JSONRPC"}
    interface = AgentInterface.model_validate({**data, "tenant": "acme"})

    assert interface.protocol_binding == "JSONRPC"
    assert interface.model_dump(mode="json") == {**_INTERFACE, "tenant": "acme"}


def test_skill_with_optional_lists_round_trips() -> None:
    data = {
        **_SKILL,
        "examples": ["Find papers on JCS"],
        "inputModes": ["text/plain"],
        "outputModes": ["application/json"],
    }

    assert AgentSkill.model_validate(data).model_dump(mode="json") == data


def test_unset_and_null_optional_fields_are_not_dumped() -> None:
    skill = AgentSkill.model_validate({**_SKILL, "examples": None})

    assert skill.model_dump(mode="json") == _SKILL
    assert AgentCapabilities().model_dump(mode="json") == {}


def test_unknown_fields_are_ignored_on_read() -> None:
    skill = AgentSkill.model_validate({**_SKILL, "futureField": {"x": 1}})

    assert skill.model_dump(mode="json") == _SKILL


@pytest.mark.parametrize("field", ["id", "name", "description"])
def test_skill_rejects_missing_or_empty_required_string(field: str) -> None:
    missing = {k: v for k, v in _SKILL.items() if k != field}

    with pytest.raises(ValidationError):
        AgentSkill.model_validate(missing)
    with pytest.raises(ValidationError):
        AgentSkill.model_validate({**_SKILL, field: ""})


@pytest.mark.parametrize("tags", [[], [""]])
def test_skill_tags_must_hold_a_non_empty_tag(tags: list[str]) -> None:
    with pytest.raises(ValidationError):
        AgentSkill.model_validate({**_SKILL, "tags": tags})


@pytest.mark.parametrize("field", ["url", "protocolBinding", "protocolVersion"])
def test_interface_rejects_empty_required_string(field: str) -> None:
    with pytest.raises(ValidationError):
        AgentInterface.model_validate({**_INTERFACE, field: ""})


def test_provider_requires_url_and_organization() -> None:
    with pytest.raises(ValidationError):
        AgentProvider.model_validate({"url": "https://example.com"})


def test_explicit_false_capability_is_kept() -> None:
    capabilities = AgentCapabilities.model_validate({"streaming": False})

    assert capabilities.streaming is False
    assert capabilities.push_notifications is None
    assert capabilities.model_dump(mode="json") == {"streaming": False}


def test_capability_flags_reject_non_boolean_values() -> None:
    with pytest.raises(ValidationError):
        AgentCapabilities.model_validate({"streaming": "true"})


def test_extension_required_false_is_omitted_and_true_is_kept() -> None:
    params = {"agent_urn": "urn:asap:agent:research", "nested": {"n": 1}}
    optional = AgentExtension.model_validate({"uri": "https://x.example/ext", "required": False})
    mandatory = AgentExtension(uri="https://x.example/ext", required=True, params=params)

    assert optional.model_dump(mode="json") == {"uri": "https://x.example/ext"}
    assert mandatory.model_dump(mode="json") == {
        "uri": "https://x.example/ext",
        "required": True,
        "params": params,
    }
    assert AgentExtension.model_validate({}).model_dump(mode="json") == {}  # nothing required


def test_signature_round_trips_and_requires_protected_and_signature() -> None:
    data = {"protected": "eyJhbGciOiJFZERTQSJ9", "signature": "c2ln", "header": {"kid": "k1"}}

    assert AgentCardSignature.model_validate(data).model_dump(mode="json") == data
    with pytest.raises(ValidationError):
        AgentCardSignature.model_validate({"protected": "eyJhbGciOiJFZERTQSJ9", "signature": ""})


def test_models_are_frozen() -> None:
    interface = AgentInterface.model_validate(_INTERFACE)

    with pytest.raises(ValidationError):
        interface.url = "https://other.example"
