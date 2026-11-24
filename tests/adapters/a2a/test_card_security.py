"""Tests for the security inputs of manifest_to_agent_card (fields no signature covers)."""

from __future__ import annotations

from typing import Any

import pytest

from asap.adapters.a2a import (
    A2AMappingInputError,
    SecurityRequirement,
    SecurityScheme,
    manifest_to_agent_card,
)
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_SCHEMES: dict[str, dict[str, Any]] = {
    "bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer", "bearerFormat": "JWT"}},
    "oauth": {
        "oauth2SecurityScheme": {
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://idp.example.com/token",
                    "scopes": {"agent:read": "Read agent data"},
                }
            }
        }
    },
}
_OAUTH_READ = {"schemes": {"oauth": {"list": ["agent:read"]}}}


def _card_json(schemes: dict[str, Any], requirements: list[dict[str, Any]]) -> dict[str, Any]:
    inputs = research_card_inputs(
        security_schemes={name: SecurityScheme.model_validate(s) for name, s in schemes.items()},
        security_requirements=[SecurityRequirement.model_validate(r) for r in requirements],
    )
    card = manifest_to_agent_card(research_manifest(), inputs).card
    dumped: dict[str, Any] = card.model_dump(mode="json")
    return dumped


def test_security_inputs_reach_the_card() -> None:
    requirements = [_OAUTH_READ, {"schemes": {"bearer": {"list": ["agent:invoke"]}}}]

    card_json = _card_json(_SCHEMES, requirements)

    assert card_json["securitySchemes"] == _SCHEMES
    assert card_json["securityRequirements"] == requirements


def test_an_mtls_scheme_without_a_description_gets_one() -> None:
    schemes = {
        "mtls": {"mtlsSecurityScheme": {}},
        "mtlsNamed": {"mtlsSecurityScheme": {"description": "CA"}},
    }

    card_json = _card_json(schemes, [])

    assert card_json["securitySchemes"] == {
        "mtls": {"mtlsSecurityScheme": {"description": "Mutual TLS client authentication"}},
        "mtlsNamed": {"mtlsSecurityScheme": {"description": "CA"}},
    }


@pytest.mark.parametrize(
    "requirement",
    [
        {"schemes": {"bearer": {}}},
        {"schemes": {"bearer": {"list": []}}},
        {"schemes": {"bearer": {"list": [""]}}},
        {"schemes": {"oauth": {"list": ["agent:read"]}, "bearer": {}}},
        {"schemes": {}},
    ],
    ids=["no-list", "empty-list", "empty-scope", "one-scheme-without-scope", "no-scheme"],
)
def test_a_requirement_that_a_signature_would_not_cover_is_refused(
    requirement: dict[str, Any],
) -> None:
    with pytest.raises(A2AMappingInputError, match="signature") as excinfo:
        _card_json(_SCHEMES, [requirement])

    assert excinfo.value.input_name == "security_requirements"


def test_a_requirement_that_names_an_undeclared_scheme_is_refused() -> None:
    with pytest.raises(A2AMappingInputError, match="'apiKey'") as excinfo:
        _card_json(_SCHEMES, [{"schemes": {"apiKey": {"list": ["x"]}}}])

    assert excinfo.value.input_name == "security_requirements"


@pytest.mark.parametrize(
    "flows",
    [
        {"implicit": {"authorizationUrl": "https://idp.example.com/a", "scopes": {"s": "S"}}},
        {"password": {"tokenUrl": "https://idp.example.com/t", "scopes": {"s": "S"}}},
    ],
    ids=["implicit", "password"],
)
def test_deprecated_flows_are_refused(flows: dict[str, Any]) -> None:
    with pytest.raises(A2AMappingInputError, match="deprecated") as excinfo:
        _card_json({"legacy": {"oauth2SecurityScheme": {"flows": flows}}}, [])

    assert excinfo.value.input_name == "security_schemes"


def test_empty_security_inputs_are_not_emitted() -> None:
    card_json = _card_json({}, [])

    assert "securitySchemes" not in card_json
    assert "securityRequirements" not in card_json
