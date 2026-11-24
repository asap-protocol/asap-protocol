"""Tests for the A2A v1.0 security scheme, OAuth flow, and security requirement models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import AgentCard, OAuthFlows, SecurityRequirement, SecurityScheme
from tests.adapters.a2a.helpers import load_card_fixture

_SCOPES = {"agent:read": "Read agent data"}
_AUTHORIZE = "https://idp.example.com/authorize"
_TOKEN = "https://idp.example.com/token"
_FLOWS: dict[str, dict[str, Any]] = {
    "authorizationCode": {
        "authorizationUrl": _AUTHORIZE,
        "tokenUrl": _TOKEN,
        "scopes": _SCOPES,
        "pkceRequired": True,
    },
    "clientCredentials": {"tokenUrl": _TOKEN, "scopes": _SCOPES},
    "deviceCode": {
        "deviceAuthorizationUrl": "https://idp.example.com/device",
        "tokenUrl": _TOKEN,
        "scopes": _SCOPES,
    },
    "implicit": {"authorizationUrl": _AUTHORIZE, "scopes": _SCOPES},
    "password": {"tokenUrl": _TOKEN, "scopes": _SCOPES},
}


_SCHEMES: dict[str, dict[str, Any]] = {
    "apiKey": {"apiKeySecurityScheme": {"location": "header", "name": "X-API-Key"}},
    "bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer", "bearerFormat": "JWT"}},
    "oidc": {
        "openIdConnectSecurityScheme": {
            "openIdConnectUrl": "https://idp.example.com/.well-known/openid-configuration"
        }
    },
    "mtls": {"mtlsSecurityScheme": {"description": "Client certificate"}},
    "oauth": {
        "oauth2SecurityScheme": {
            "flows": {"clientCredentials": _FLOWS["clientCredentials"]},
            "oauth2MetadataUrl": "https://idp.example.com/.well-known/oauth-authorization-server",
        }
    },
}


@pytest.mark.parametrize("flow", sorted(_FLOWS))
def test_each_flow_parses_and_dumps_unchanged(flow: str) -> None:
    """The deprecated ``implicit`` and ``password`` flows parse on read too."""
    data = {flow: _FLOWS[flow]}

    assert OAuthFlows.model_validate(data).model_dump(mode="json") == data


@pytest.mark.parametrize(
    "data",
    [{"clientCredentials": _FLOWS["clientCredentials"], "deviceCode": _FLOWS["deviceCode"]}, {}],
    ids=["two-flows", "no-flow"],
)
def test_oauth_flows_need_exactly_one_flow(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        OAuthFlows.model_validate(data)


def test_pkce_required_false_is_omitted_on_dump() -> None:
    flow = {k: v for k, v in _FLOWS["authorizationCode"].items() if k != "pkceRequired"}

    flows = OAuthFlows.model_validate({"authorizationCode": {**flow, "pkceRequired": False}})

    assert flows.authorization_code is not None
    assert flows.authorization_code.pkce_required is False
    assert flows.model_dump(mode="json") == {"authorizationCode": flow}


@pytest.mark.parametrize("scopes", [None, {}])
def test_oauth_flow_requires_scopes(scopes: dict[str, str] | None) -> None:
    with pytest.raises(ValidationError):
        OAuthFlows.model_validate({"clientCredentials": {"tokenUrl": _TOKEN, "scopes": scopes}})


def test_oauth_flow_requires_its_urls() -> None:
    with pytest.raises(ValidationError):
        OAuthFlows.model_validate({"deviceCode": {"tokenUrl": _TOKEN, "scopes": _SCOPES}})


@pytest.mark.parametrize("name", sorted(_SCHEMES))
def test_each_scheme_wrapper_parses_and_dumps_unchanged(name: str) -> None:
    data = _SCHEMES[name]

    assert SecurityScheme.model_validate(data).model_dump(mode="json") == data


def test_wrapper_exposes_the_scheme_under_its_snake_case_name() -> None:
    scheme = SecurityScheme.model_validate(_SCHEMES["oidc"])

    assert scheme.open_id_connect_security_scheme is not None
    assert scheme.open_id_connect_security_scheme.open_id_connect_url.startswith("https://idp")
    assert scheme.mtls_security_scheme is None


@pytest.mark.parametrize(
    "data",
    [{**_SCHEMES["bearer"], **_SCHEMES["mtls"]}, {}, {"type": "http", "scheme": "bearer"}],
    ids=["two-wrapper-keys", "no-wrapper-key", "v0.3-type-field"],
)
def test_scheme_needs_exactly_one_wrapper_key(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        SecurityScheme.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"apiKeySecurityScheme": {"location": "header"}},
        {"httpAuthSecurityScheme": {"scheme": ""}},
        {"openIdConnectSecurityScheme": {}},
        {"oauth2SecurityScheme": {}},
    ],
    ids=["api-key-name", "http-scheme", "oidc-url", "oauth2-flows"],
)
def test_scheme_rejects_a_missing_required_field(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SecurityScheme.model_validate(data)


def test_requirement_with_scopes_round_trips() -> None:
    data: dict[str, Any] = {"schemes": {"oauth": {"list": ["agent:read"]}, "mtls": {"list": []}}}

    requirement = SecurityRequirement.model_validate(data)

    assert requirement.schemes is not None
    assert requirement.schemes["oauth"].scopes == ["agent:read"]
    assert requirement.model_dump(mode="json") == data


def test_requirement_without_a_list_key_is_accepted() -> None:
    data: dict[str, Any] = {"schemes": {"bearer": {}}}

    requirement = SecurityRequirement.model_validate(data)

    assert requirement.schemes is not None
    assert requirement.schemes["bearer"].scopes is None
    assert requirement.model_dump(mode="json") == data


def test_full_card_with_every_scheme_type_parses_and_dumps_unchanged() -> None:
    data = load_card_fixture("full_card.json")

    card = AgentCard.model_validate(data)

    assert card.security_schemes is not None
    wrapper_keys = {key for scheme in data["securitySchemes"].values() for key in scheme}
    assert len(wrapper_keys) == len(SecurityScheme.model_fields)
    assert card.model_dump(mode="json") == data


def test_card_and_skill_security_requirements_are_parsed() -> None:
    card = AgentCard.model_validate(load_card_fixture("full_card.json"))

    assert card.security_requirements is not None
    assert [sorted(r.schemes or {}) for r in card.security_requirements] == [
        ["oauth"],
        ["mtls", "oidc"],
    ]
    skill_requirements = card.skills[0].security_requirements
    assert skill_requirements is not None
    assert skill_requirements[0].schemes is not None
    assert skill_requirements[0].schemes["oauth"].scopes == ["agent:research"]
