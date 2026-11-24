"""Tests for manifest_to_agent_card: ASAP manifest → A2A v1.0 Agent Card."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import (
    ASAP_EXTENSION_URI,
    A2AMappingError,
    A2AMappingInputError,
    AgentCardInputs,
    AgentInterface,
    AgentProvider,
    manifest_to_agent_card,
)
from asap.models.entities import Capability, Endpoint, Manifest, Skill

_INTERFACE = AgentInterface(
    url="https://agent.example.com/a2a", protocol_binding="JSONRPC", protocol_version="1.0"
)
_INPUT_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


def _manifest(**overrides: Any) -> Manifest:
    skills = [
        Skill(id="web_research", description="Search the web", input_schema=_INPUT_SCHEMA),
        Skill(id="summarize", description="Summarize a document"),
    ]
    fields: dict[str, Any] = {
        "id": "urn:asap:agent:research",
        "name": "Research Agent",
        "version": "1.2.0",
        "description": "Performs web research",
        "capabilities": Capability(skills=skills, state_persistence=True, streaming=True),
        "endpoints": Endpoint(asap="https://agent.example.com/asap"),
    }
    return Manifest(**{**fields, **overrides})


def _inputs(**overrides: Any) -> AgentCardInputs:
    fields: dict[str, Any] = {
        "interfaces": [_INTERFACE],
        "default_input_modes": ["application/json"],
        "default_output_modes": ["application/json"],
        "skill_tags": {"web_research": ["research"], "summarize": ["summary"]},
        "skill_names": {"web_research": "Web research"},
    }
    return AgentCardInputs(**{**fields, **overrides})


def _tagged_inputs(manifest: Manifest) -> AgentCardInputs:
    """Inputs that tag exactly the manifest's own skills."""
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    return _inputs(skill_tags={skill_id: ["tag"] for skill_id in skill_ids}, skill_names={})


def test_every_mapped_field_reaches_the_card() -> None:
    card = manifest_to_agent_card(_manifest(), _inputs()).card

    assert card.model_dump(mode="json") == {
        "name": "Research Agent",
        "description": "Performs web research",
        "supportedInterfaces": [_INTERFACE.model_dump(mode="json")],
        "version": "1.2.0",
        "capabilities": {
            "streaming": True,
            "extensions": [
                {
                    "uri": ASAP_EXTENSION_URI,
                    "description": card.capabilities.extensions[0].description,
                    "params": {
                        "agent_urn": "urn:asap:agent:research",
                        "asap_endpoint": "https://agent.example.com/asap",
                        "asap_version": "0.1",
                        "state_persistence": True,
                        "skill_schemas": {"web_research": {"input_schema": _INPUT_SCHEMA}},
                    },
                }
            ],
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "web_research",
                "name": "Web research",
                "description": "Search the web",
                "tags": ["research"],
            },
            {
                "id": "summarize",
                "name": "summarize",
                "description": "Summarize a document",
                "tags": ["summary"],
            },
        ],
    }


def test_events_endpoint_and_optional_inputs_reach_the_card() -> None:
    manifest = _manifest(
        endpoints=Endpoint(asap="https://a.example/asap", events="wss://a.example/ev")
    )
    provider = AgentProvider(url="https://example.com", organization="Example Org")
    inputs = _inputs(
        provider=provider, documentation_url="https://d.example", icon_url="https://i.example"
    )

    card = manifest_to_agent_card(manifest, inputs).card

    assert card.provider == provider
    assert (card.documentation_url, card.icon_url) == ("https://d.example", "https://i.example")
    assert card.capabilities.extensions is not None
    assert card.capabilities.extensions[0].params is not None
    assert card.capabilities.extensions[0].params["events_endpoint"] == "wss://a.example/ev"


def test_streaming_false_is_written_explicitly() -> None:
    manifest = _manifest(capabilities=Capability(skills=[Skill(id="summarize", description="S")]))

    card = manifest_to_agent_card(manifest, _tagged_inputs(manifest)).card

    assert card.model_dump(mode="json")["capabilities"]["streaming"] is False


def test_a_skill_without_tags_raises_a_typed_input_error() -> None:
    with pytest.raises(A2AMappingInputError, match="'summarize'") as excinfo:
        manifest_to_agent_card(_manifest(), _inputs(skill_tags={"web_research": ["research"]}))

    assert excinfo.value.input_name == "skill_tags"
    assert excinfo.value.code == "asap:adapters/a2a/mapping_input"


@pytest.mark.parametrize(("input_name", "value"), [("skill_tags", ["x"]), ("skill_names", "X")])
def test_inputs_for_a_skill_the_manifest_lacks_raise(input_name: str, value: Any) -> None:
    current = _inputs().model_dump()[input_name]

    with pytest.raises(A2AMappingInputError, match="'typo'") as excinfo:
        manifest_to_agent_card(_manifest(), _inputs(**{input_name: {**current, "typo": value}}))

    assert excinfo.value.input_name == input_name


@pytest.mark.parametrize(
    "overrides",
    [
        {"interfaces": []},
        {"default_input_modes": []},
        {"default_output_modes": [""]},
        {"skill_tags": {"web_research": [], "summarize": ["summary"]}},
        {"documentation_url": ""},
    ],
)
def test_inputs_reject_empty_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _inputs(**overrides)


@pytest.mark.parametrize(
    ("manifest", "fields"),
    [
        (_manifest(description=""), ("description",)),
        (_manifest(capabilities=Capability(skills=[])), ("skills",)),
        (
            _manifest(capabilities=Capability(skills=[Skill(id="summarize", description="")])),
            ("skills.0.description",),
        ),
        (_manifest(endpoints=Endpoint(asap="")), ("asap_endpoint",)),
    ],
    ids=["empty-description", "no-skills", "empty-skill-description", "empty-endpoint"],
)
def test_no_empty_required_field_is_ever_emitted(manifest: Manifest, fields: tuple[str]) -> None:
    with pytest.raises(A2AMappingError, match="urn:asap:agent:research") as excinfo:
        manifest_to_agent_card(manifest, _tagged_inputs(manifest))

    assert excinfo.value.code == "asap:adapters/a2a/mapping"
    assert excinfo.value.fields == fields


def test_duplicate_skill_ids_raise() -> None:
    skills = [Skill(id="summarize", description="A"), Skill(id="summarize", description="B")]

    with pytest.raises(A2AMappingError, match="'summarize'"):
        manifest_to_agent_card(_manifest(capabilities=Capability(skills=skills)), _inputs())
