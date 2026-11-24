"""Interop with a2a-sdk: cards from asap.adapters.a2a parse in the SDK's protobuf AgentCard.

The SDK parses with unknown fields not ignored, so a field outside the A2A v1.0
proto fails the test. a2a-sdk is a dev dependency only.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError

from asap.adapters.a2a import AgentCard, SecurityRequirement, SecurityScheme, manifest_to_agent_card
from tests.adapters.a2a.helpers import load_card_fixture, research_card_inputs, research_manifest


def _strict_sdk_parse(card_json: dict[str, Any]) -> SdkAgentCard:
    return ParseDict(card_json, SdkAgentCard(), ignore_unknown_fields=False)


def test_a_generated_card_parses_strictly_in_the_sdk_without_loss() -> None:
    oidc_url = "https://idp.example.com/.well-known/openid-configuration"
    inputs = research_card_inputs(
        security_schemes={
            "oidc": SecurityScheme.model_validate(
                {"openIdConnectSecurityScheme": {"openIdConnectUrl": oidc_url}}
            ),
            "mtls": SecurityScheme.model_validate({"mtlsSecurityScheme": {}}),
        },
        security_requirements=[
            SecurityRequirement.model_validate({"schemes": {"oidc": {"list": ["agent:read"]}}})
        ],
    )
    card_json = manifest_to_agent_card(research_manifest(), inputs).card.model_dump(mode="json")

    assert MessageToDict(_strict_sdk_parse(card_json)) == card_json


@pytest.mark.parametrize("name", ["minimal_card.json", "full_card.json"])
def test_fixture_cards_parse_strictly_and_the_sdk_dumps_what_the_models_dump(name: str) -> None:
    data = load_card_fixture(name)

    sdk_json = MessageToDict(_strict_sdk_parse(data))

    assert sdk_json == AgentCard.model_validate(data).model_dump(mode="json")


def test_the_v03_fixture_fails_the_strict_sdk_parse_too() -> None:
    with pytest.raises(ParseError):
        _strict_sdk_parse(load_card_fixture("v03_card.json"))
