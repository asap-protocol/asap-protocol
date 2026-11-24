"""Per-skill security requirements in manifest_to_agent_card.

A skill's ``securityRequirements`` follow the card-level rules: every scheme
named is declared, and each lists at least one non-empty scope, so a card
signature covers the whole requirement.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from google.protobuf.json_format import ParseDict

from asap.adapters.a2a import (
    A2AMappingInputError,
    SecurityRequirement,
    SecurityScheme,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_OIDC = {"openIdConnectSecurityScheme": {"openIdConnectUrl": "https://idp.example"}}
_SEARCH_SCOPE = {"schemes": {"idp": {"list": ["research:search"]}}}


def _card_json(skill_requirements: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    inputs = research_card_inputs(
        security_schemes={"idp": SecurityScheme.model_validate(_OIDC)},
        skill_security_requirements={
            skill_id: [SecurityRequirement.model_validate(r) for r in requirements]
            for skill_id, requirements in skill_requirements.items()
        },
    )
    dumped: dict[str, Any] = manifest_to_agent_card(research_manifest(), inputs).card.model_dump(
        mode="json"
    )
    return dumped


def test_a_skill_requirement_reaches_only_its_skill() -> None:
    card_json = _card_json({"web_research": [_SEARCH_SCOPE]})

    skills = {skill["id"]: skill for skill in card_json["skills"]}
    assert skills["web_research"]["securityRequirements"] == [_SEARCH_SCOPE]
    assert "securityRequirements" not in skills["summarize"]
    assert "securityRequirements" not in card_json


def test_the_card_with_skill_requirements_parses_strictly_in_the_sdk() -> None:
    card_json = _card_json({"web_research": [_SEARCH_SCOPE]})

    sdk_card = ParseDict(card_json, SdkAgentCard(), ignore_unknown_fields=False)

    requirement = sdk_card.skills[0].security_requirements[0]
    assert list(requirement.schemes["idp"].list) == ["research:search"]


def test_skill_requirements_are_dropped_on_the_way_back() -> None:
    card = manifest_to_agent_card(
        research_manifest(),
        research_card_inputs(
            security_schemes={"idp": SecurityScheme.model_validate(_OIDC)},
            skill_security_requirements={
                "web_research": [SecurityRequirement.model_validate(_SEARCH_SCOPE)]
            },
        ),
    ).card

    dropped = agent_card_to_manifest(card).dropped

    assert "skills[0].securityRequirements" in [field.path for field in dropped]


def test_an_unknown_skill_id_is_refused() -> None:
    with pytest.raises(A2AMappingInputError, match="'translate'") as caught:
        _card_json({"translate": [_SEARCH_SCOPE]})

    assert caught.value.input_name == "skill_security_requirements"


@pytest.mark.parametrize(
    ("requirement", "problem"),
    [
        ({"schemes": {"other": {"list": ["x"]}}}, "not declared"),
        ({"schemes": {"idp": {}}}, "no scope"),
        ({"schemes": {"idp": {"list": [""]}}}, "no scope"),
        ({"schemes": {}}, "names no scheme"),
    ],
)
def test_a_skill_requirement_breaking_the_card_rules_is_refused(
    requirement: dict[str, Any], problem: str
) -> None:
    with pytest.raises(A2AMappingInputError, match=problem) as caught:
        _card_json({"summarize": [requirement]})

    assert caught.value.input_name == "skill_security_requirements"
    assert "skill_security_requirements['summarize'][0]" in str(caught.value)
