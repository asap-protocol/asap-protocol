"""Tests for the dropped-field reports and the unmapped-field tables."""

from __future__ import annotations

from asap.adapters.a2a import (
    UNMAPPED_CARD_FIELDS,
    UNMAPPED_MANIFEST_FIELDS,
    AgentCard,
    DroppedField,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from asap.models.entities import (
    AuthScheme,
    Capability,
    Endpoint,
    HardwareCapability,
    InferenceCapability,
    Manifest,
    SLADefinition,
    VerificationStatus,
)
from asap.models.enums import InferenceMode, VerificationState
from tests.adapters.a2a.helpers import (
    load_card_fixture,
    research_card_inputs,
    research_manifest,
)

_URN = "urn:asap:agent:research"
_ENDPOINTS = Endpoint(asap="https://gateway.example.com/asap")


def test_a_manifest_with_only_mapped_fields_drops_nothing() -> None:
    assert manifest_to_agent_card(research_manifest(), research_card_inputs()).dropped == ()


def test_every_unmapped_manifest_field_is_reported_with_its_reason() -> None:
    skills = research_manifest().capabilities.skills
    manifest = research_manifest(
        supported_versions=["2.1", "2.2"],
        auth=AuthScheme(schemes=["bearer"]),
        signature="manifest-signature",
        sla=SLADefinition(availability="99.5%"),
        verification=VerificationStatus(status=VerificationState.PENDING),
        ttl_seconds=60,
        capabilities=Capability(
            skills=skills,
            mcp_tools=["web_search"],
            hardware=HardwareCapability(model="board"),
            inference=InferenceCapability(modes=[InferenceMode.CLOUD]),
        ),
    )

    dropped = manifest_to_agent_card(manifest, research_card_inputs()).dropped

    assert dropped == tuple(
        DroppedField(path=field, field=field, reason=reason)
        for field, reason in UNMAPPED_MANIFEST_FIELDS.items()
    )


def test_unmapped_manifest_rows_name_real_fields_and_give_a_reason() -> None:
    for field, reason in UNMAPPED_MANIFEST_FIELDS.items():
        owner, _, name = field.rpartition(".")
        model = Capability if owner == "capabilities" else Manifest
        assert name in model.model_fields, field
        assert reason.strip(), field


def test_every_unmapped_card_field_is_reported_with_its_reason() -> None:
    card = AgentCard.model_validate(load_card_fixture("full_card.json"))

    dropped = agent_card_to_manifest(card, agent_id=_URN, endpoints=_ENDPOINTS).dropped

    assert [d.path for d in dropped] == [
        "supportedInterfaces",
        "provider",
        "documentationUrl",
        "iconUrl",
        "capabilities.pushNotifications",
        "capabilities.extendedAgentCard",
        "capabilities.extensions[0]",
        "securitySchemes",
        "securityRequirements",
        "defaultInputModes",
        "defaultOutputModes",
        "skills[0].name",
        "skills[1].name",
        "skills[0].tags",
        "skills[1].tags",
        "skills[0].examples",
        "skills[0].inputModes",
        "skills[0].outputModes",
        "skills[0].securityRequirements",
        "signatures",
    ]
    assert {d.field for d in dropped} == set(UNMAPPED_CARD_FIELDS)
    assert all(d.reason == UNMAPPED_CARD_FIELDS[d.field] for d in dropped)


def test_the_asap_extension_and_a_skill_name_equal_to_its_id_are_not_reported() -> None:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card

    dropped = agent_card_to_manifest(card).dropped

    assert [d.path for d in dropped] == [
        "supportedInterfaces",
        "defaultInputModes",
        "defaultOutputModes",
        "skills[0].name",
        "skills[0].tags",
        "skills[1].tags",
    ]
