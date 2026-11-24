"""Tests for the A2A v1.0 security scheme, OAuth flow, and security requirement models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.adapters.a2a import OAuthFlows

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
