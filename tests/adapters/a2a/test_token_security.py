"""Card security derived from an external-token config.

Each issuer has an OIDC or HTTP Bearer scheme. Each matching scope rule adds
an alternative to the skill and to the card. Claim rules stay in the gateway.
"""

from __future__ import annotations

from typing import Any

import pytest
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


def test_oidc_discovery_yields_an_openid_connect_scheme() -> None:
    issuer = "https://idp.example/t/"
    config = _config(
        {"issuer": issuer, "audience": _AUDIENCE, "key_source": {"kind": "oidc_discovery"}}
    )

    security = external_token_card_security(config, _SKILLS)

    schemes = {name: s.model_dump(mode="json") for name, s in security.security_schemes.items()}
    assert schemes == {
        issuer: {
            "openIdConnectSecurityScheme": {
                "openIdConnectUrl": "https://idp.example/t/.well-known/openid-configuration",
                "description": f"Bearer access token from {issuer} with audience {_AUDIENCE}",
            }
        },
    }


@pytest.mark.parametrize(
    "verification_config",
    [
        {"key_source": LocalIssuer().config_entry(_AUDIENCE)["key_source"]},
        {"key_source": {"kind": "jwks_uri", "jwks_uri": "https://idp.example/jwks"}},
        {
            "introspection": {
                "url": "https://idp.example/introspect",
                "client_id": "test-client",
                "client_secret": "test-secret",
            }
        },
    ],
    ids=["static-jwks", "remote-jwks", "introspection"],
)
def test_token_issuer_without_oidc_discovery_yields_http_bearer(
    verification_config: dict[str, Any],
) -> None:
    issuer = "https://idp.example"
    config = _config({"issuer": issuer, "audience": _AUDIENCE, **verification_config})

    scheme = external_token_card_security(config, _SKILLS).security_schemes[issuer]

    assert scheme.model_dump(mode="json") == {
        "httpAuthSecurityScheme": {
            "scheme": "Bearer",
            "description": f"Bearer access token from {issuer} with audience {_AUDIENCE}",
        }
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


def test_a_scope_that_grants_two_skills_is_listed_once_at_card_level() -> None:
    idp = LocalIssuer()
    rules = _rules(("all", "web_research"), ("all", "summarize"))
    config = _config(idp.config_entry(_AUDIENCE, grant_rules=rules))

    security = external_token_card_security(config, _SKILLS)

    one = [_requirement(idp.issuer, "all")]
    assert {
        skill: [r.model_dump(mode="json") for r in requirements]
        for skill, requirements in security.skill_security_requirements.items()
    } == {"web_research": one, "summarize": one}
    assert [r.model_dump(mode="json") for r in security.security_requirements] == one


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
    idp = LocalIssuer()
    claim_rule = {"claim": "groups", "value": "researchers", "capability": "web_research"}
    config = _config(idp.config_entry(_AUDIENCE, grant_rules=[claim_rule]))

    security = external_token_card_security(config, _SKILLS)

    assert list(security.security_schemes) == [idp.issuer]
    scheme = security.security_schemes[idp.issuer].http_auth_security_scheme
    assert scheme is not None and scheme.scheme == "Bearer"
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
    assert sdk_card.security_schemes[idp.issuer].http_auth_security_scheme.scheme == "Bearer"
    assert list(sdk_card.security_requirements[0].schemes[idp.issuer].list) == ["search"]
    assert list(sdk_card.skills[0].security_requirements[0].schemes[idp.issuer].list) == ["search"]
