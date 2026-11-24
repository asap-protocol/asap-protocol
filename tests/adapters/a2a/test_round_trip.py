"""Round trips between an A2A v1.0 Agent Card and an ASAP manifest (COMPAT-005 acceptance)."""

from __future__ import annotations

from typing import Any

import pytest

from asap.adapters.a2a import (
    AgentCard,
    AgentCardInputs,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from asap.models.entities import Capability, Endpoint, Skill
from tests.adapters.a2a.helpers import (
    INPUT_SCHEMA,
    load_card_fixture,
    research_card_inputs,
    research_manifest,
)


def _inputs_from(card: AgentCard) -> AgentCardInputs:
    """The caller inputs that give back the card fields a manifest does not hold."""
    return AgentCardInputs(
        interfaces=card.supported_interfaces,
        default_input_modes=card.default_input_modes,
        default_output_modes=card.default_output_modes,
        skill_tags={skill.id: skill.tags for skill in card.skills},
        skill_names={skill.id: skill.name for skill in card.skills},
        provider=card.provider,
        documentation_url=card.documentation_url,
        icon_url=card.icon_url,
        security_schemes=card.security_schemes,
        security_requirements=card.security_requirements,
    )


def _comparable(card: AgentCard) -> dict[str, Any]:
    """The card without the fields that neither a manifest nor the caller inputs carry."""
    data: dict[str, Any] = card.model_dump(mode="json")
    data.pop("signatures", None)
    data["capabilities"] = {"streaming": data["capabilities"].get("streaming", False)}
    for skill in data["skills"]:
        for key in ("examples", "inputModes", "outputModes", "securityRequirements"):
            skill.pop(key, None)
    return data


def test_card_to_manifest_to_card_keeps_every_mapped_field() -> None:
    original = AgentCard.model_validate(load_card_fixture("full_card.json"))
    endpoints = Endpoint(asap="https://gateway.example.com/asap")

    manifest = agent_card_to_manifest(
        original, agent_id="urn:asap:agent:research", endpoints=endpoints
    ).manifest
    regenerated = manifest_to_agent_card(manifest, _inputs_from(original)).card

    assert _comparable(regenerated) == _comparable(original)


def test_manifest_to_card_to_manifest_returns_the_same_manifest() -> None:
    skills = [
        Skill(
            id="web_research",
            description="Search the web",
            input_schema=INPUT_SCHEMA,
            output_schema={"type": "string"},
        ),
        Skill(id="summarize", description="Summarize a document"),
    ]
    manifest = research_manifest(
        endpoints=Endpoint(asap="https://a.example/asap", events="wss://a.example/events"),
        capabilities=Capability(skills=skills, state_persistence=True, streaming=True),
    )

    card = manifest_to_agent_card(manifest, research_card_inputs()).card

    assert agent_card_to_manifest(card).manifest == manifest


@pytest.mark.parametrize("asap_streaming", [False, True])
def test_streaming_override_preserves_asap_streaming(asap_streaming: bool) -> None:
    capabilities = Capability(
        skills=research_manifest().capabilities.skills, streaming=asap_streaming
    )
    manifest = research_manifest(capabilities=capabilities)
    inputs = research_card_inputs(a2a_streaming=not asap_streaming)

    card = manifest_to_agent_card(manifest, inputs).card

    assert card.capabilities.streaming == (not asap_streaming)
    assert agent_card_to_manifest(card).manifest == manifest


def test_an_asap_card_survives_json_and_card_to_manifest_to_card_unchanged() -> None:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    parsed = AgentCard.model_validate_json(card.model_dump_json())

    manifest = agent_card_to_manifest(parsed).manifest

    assert manifest_to_agent_card(manifest, _inputs_from(parsed)).card == card
