"""Shared helpers for the A2A adapter tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from asap.adapters.a2a import AgentCardInputs, AgentInterface
from asap.models.entities import Capability, Endpoint, Manifest, Skill

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_card_fixture(name: str) -> dict[str, Any]:
    """Return the JSON object stored in ``fixtures/<name>``."""
    data: dict[str, Any] = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return data


INTERFACE = AgentInterface(
    url="https://agent.example.com/a2a", protocol_binding="JSONRPC", protocol_version="1.0"
)
INPUT_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


def research_manifest(**overrides: Any) -> Manifest:
    """A manifest with two skills, one with an input schema; *overrides* replace fields."""
    skills = [
        Skill(id="web_research", description="Search the web", input_schema=INPUT_SCHEMA),
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


def research_card_inputs(**overrides: Any) -> AgentCardInputs:
    """Card inputs that match :func:`research_manifest`; *overrides* replace fields."""
    fields: dict[str, Any] = {
        "interfaces": [INTERFACE],
        "default_input_modes": ["application/json"],
        "default_output_modes": ["application/json"],
        "skill_tags": {"web_research": ["research"], "summarize": ["summary"]},
        "skill_names": {"web_research": "Web research"},
    }
    return AgentCardInputs(**{**fields, **overrides})


def tagged_card_inputs(manifest: Manifest) -> AgentCardInputs:
    """Card inputs that tag exactly the manifest's own skills."""
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    return research_card_inputs(
        skill_tags={skill_id: ["tag"] for skill_id in skill_ids}, skill_names={}
    )
