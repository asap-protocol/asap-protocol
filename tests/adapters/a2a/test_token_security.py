"""Card security derived from an external-token config.

Each issuer is one ``openIdConnectSecurityScheme``; each scope rule for a card
skill is one alternative on that skill; the card-level list is their union.
Claim rules are not advertised: the card may say less than the gateway
accepts, never more.
"""

from __future__ import annotations

from typing import Any

from a2a.types import AgentCard as SdkAgentCard
from google.protobuf.json_format import ParseDict

from asap.adapters.a2a import external_token_card_security, manifest_to_agent_card
from asap.auth.external_tokens import ExternalTokenConfig
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.auth.local_issuer import LocalIssuer

_AUDIENCE = "https://agent.example.com/a2a"
_SKILLS = ("web_research", "summarize")


def _config(*entries: dict[str, Any]) -> ExternalTokenConfig:
    return ExternalTokenConfig.model_validate({"issuers": list(entries)})


def _rules(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"scope": scope, "capability": capability} for scope, capability in pairs]


def _requirement(issuer: str, scope: str) -> dict[str, Any]:
    return {"schemes": {issuer: {"list": [scope]}}}


def test_each_issuer_yields_one_openid_connect_scheme() -> None:
    first, second = LocalIssuer("https://idp-a.example"), LocalIssuer("https://idp-b.example/t/")
    config = _config(first.config_entry(_AUDIENCE), second.config_entry("https://other.example"))

    security = external_token_card_security(config, _SKILLS)

    schemes = {name: s.model_dump(mode="json") for name, s in security.security_schemes.items()}
    assert schemes == {
        "https://idp-a.example": {
            "openIdConnectSecurityScheme": {
                "openIdConnectUrl": "https://idp-a.example/.well-known/openid-configuration",
                "description": f"Bearer access token from https://idp-a.example with audience {_AUDIENCE}",
            }
        },
        "https://idp-b.example/t/": {
            "openIdConnectSecurityScheme": {
                "openIdConnectUrl": "https://idp-b.example/t/.well-known/openid-configuration",
                "description": (
                    "Bearer access token from https://idp-b.example/t/ "
                    "with audience https://other.example"
                ),
            }
        },
    }


def test_each_scope_rule_for_a_skill_is_one_alternative_on_that_skill() -> None:
    idp_a, idp_b = LocalIssuer("https://idp-a.example"), LocalIssuer("https://idp-b.example")
    rules_a = _rules(("search", "web_research"), ("read", "web_research"), ("sum", "summarize"))
    config = _config(
        idp_a.config_entry(_AUDIENCE, grant_rules=rules_a),
        idp_b.config_entry(_AUDIENCE, grant_rules=_rules(("search", "web_research"))),
    )

    security = external_token_card_security(config, _SKILLS)

    by_skill = {
        skill: [r.model_dump(mode="json") for r in requirements]
        for skill, requirements in security.skill_security_requirements.items()
    }
    assert by_skill == {
        "web_research": [
            _requirement(idp_a.issuer, "search"),
            _requirement(idp_a.issuer, "read"),
            _requirement(idp_b.issuer, "search"),
        ],
        "summarize": [_requirement(idp_a.issuer, "sum")],
    }
    assert [r.model_dump(mode="json") for r in security.security_requirements] == [
        *by_skill["web_research"],
        *by_skill["summarize"],
    ]


def test_claim_rules_and_rules_for_other_capabilities_are_not_advertised() -> None:
    idp = LocalIssuer()
    rules = [
        {"claim": "groups", "value": "researchers", "capability": "web_research"},
        {"scope": "files:read", "capability": "file.read"},
        {"scope": "sum", "capability": "summarize"},
        {"scope": "sum", "capability": "summarize"},
    ]
    config = _config(idp.config_entry(_AUDIENCE, grant_rules=rules))

    security = external_token_card_security(config, _SKILLS)

    assert list(security.skill_security_requirements) == ["summarize"]
    assert [r.model_dump(mode="json") for r in security.security_requirements] == [
        _requirement(idp.issuer, "sum")
    ]


def test_without_scope_rules_the_schemes_stay_and_no_requirement_is_made() -> None:
    config = _config(LocalIssuer().config_entry(_AUDIENCE))

    security = external_token_card_security(config, _SKILLS)

    assert list(security.security_schemes) == [LocalIssuer().issuer]
    assert (security.security_requirements, security.skill_security_requirements) == ([], {})


def test_the_derived_security_maps_to_a_card_that_parses_in_the_sdk() -> None:
    idp = LocalIssuer()
    config = _config(idp.config_entry(_AUDIENCE, grant_rules=_rules(("search", "web_research"))))
    security = external_token_card_security(config, _SKILLS)
    inputs = research_card_inputs(
        security_schemes=security.security_schemes,
        security_requirements=security.security_requirements,
        skill_security_requirements=security.skill_security_requirements,
    )

    card_json = manifest_to_agent_card(research_manifest(), inputs).card.model_dump(mode="json")

    sdk_card = ParseDict(card_json, SdkAgentCard(), ignore_unknown_fields=False)
    assert sdk_card.security_schemes[idp.issuer].HasField("open_id_connect_security_scheme")
    assert list(sdk_card.security_requirements[0].schemes[idp.issuer].list) == ["search"]
    assert list(sdk_card.skills[0].security_requirements[0].schemes[idp.issuer].list) == ["search"]
