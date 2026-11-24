"""Tests for agent_card_to_manifest: A2A v1.0 Agent Card → ASAP manifest."""

from __future__ import annotations

from typing import Any

import pytest

from asap.adapters.a2a import (
    ASAP_EXTENSION_URI,
    A2AMappingError,
    A2AMappingInputError,
    AgentCard,
    agent_card_to_manifest,
    manifest_to_agent_card,
    read_asap_extension,
)
from asap.models.entities import Endpoint
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest

_URN = "urn:asap:agent:research"
_ENDPOINTS = Endpoint(asap="https://gateway.example.com/asap")


def _asap_card(**manifest_overrides: Any) -> AgentCard:
    return manifest_to_agent_card(
        research_manifest(**manifest_overrides), research_card_inputs()
    ).card


def _card_with(**changes: Any) -> AgentCard:
    return AgentCard.model_validate({**_asap_card().model_dump(mode="json"), **changes})


def test_the_asap_extension_restores_urn_endpoints_and_asap_data_without_caller_input() -> None:
    events = Endpoint(asap="https://agent.example.com/asap", events="wss://agent.example.com/ev")

    manifest = agent_card_to_manifest(_asap_card(endpoints=events)).manifest

    assert (manifest.id, manifest.endpoints) == (_URN, events)
    assert manifest.capabilities.state_persistence is True
    assert manifest.capabilities.skills[0].input_schema is not None
    assert read_asap_extension(_asap_card()) is not None


def test_a_card_without_the_extension_maps_with_caller_input() -> None:
    card = AgentCard.model_validate(load_card_fixture("minimal_card.json"))

    manifest = agent_card_to_manifest(card, agent_id=_URN, endpoints=_ENDPOINTS).manifest

    assert (manifest.id, manifest.endpoints, manifest.version) == (_URN, _ENDPOINTS, "1.0.0")
    assert (manifest.name, manifest.description) == (card.name, card.description)
    assert [(s.id, s.description) for s in manifest.capabilities.skills] == [
        ("web_research", "Search the web and summarize the results")
    ]
    assert manifest.capabilities.streaming is False
    assert manifest.capabilities.state_persistence is False
    assert read_asap_extension(card) is None


@pytest.mark.parametrize(
    ("inputs", "missing"),
    [({}, "agent_id"), ({"agent_id": _URN}, "endpoints"), ({"endpoints": _ENDPOINTS}, "agent_id")],
)
def test_a_card_without_the_extension_requires_the_urn_and_the_endpoint(
    inputs: dict[str, Any], missing: str
) -> None:
    card = AgentCard.model_validate(load_card_fixture("minimal_card.json"))

    with pytest.raises(A2AMappingInputError, match=ASAP_EXTENSION_URI) as excinfo:
        agent_card_to_manifest(card, **inputs)

    assert excinfo.value.input_name == missing


@pytest.mark.parametrize(
    ("inputs", "input_name"),
    [({"agent_id": "urn:asap:agent:other"}, "agent_id"), ({"endpoints": _ENDPOINTS}, "endpoints")],
)
def test_caller_input_that_conflicts_with_the_extension_raises(
    inputs: dict[str, Any], input_name: str
) -> None:
    with pytest.raises(A2AMappingInputError, match="conflicts") as excinfo:
        agent_card_to_manifest(_asap_card(), **inputs)

    assert excinfo.value.input_name == input_name


def test_caller_input_that_matches_the_extension_is_accepted() -> None:
    card = _asap_card()
    endpoints = Endpoint(asap="https://agent.example.com/asap")

    assert agent_card_to_manifest(card, agent_id=_URN, endpoints=endpoints).manifest.id == _URN


def test_a_card_version_that_is_not_semver_raises_a_typed_error() -> None:
    with pytest.raises(A2AMappingError, match="'latest'") as excinfo:
        agent_card_to_manifest(_card_with(version="latest"))

    assert excinfo.value.fields == ("version",)


def test_an_extension_urn_that_is_not_an_asap_urn_raises() -> None:
    card = _asap_card()
    params = {**(read_asap_extension(card) or pytest.fail()).model_dump(), "agent_urn": "agent-1"}
    extension = {"uri": ASAP_EXTENSION_URI, "params": params}

    with pytest.raises(A2AMappingError) as excinfo:
        agent_card_to_manifest(_card_with(capabilities={"extensions": [extension]}))

    assert excinfo.value.fields == ("id",)


def test_duplicate_skill_ids_in_a_card_raise() -> None:
    skills = _asap_card().model_dump(mode="json")["skills"]

    with pytest.raises(A2AMappingError, match="'web_research'"):
        agent_card_to_manifest(_card_with(skills=[skills[0], skills[0]]))
