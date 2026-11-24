"""Tests for the ``a2a-bridge`` profile."""

from __future__ import annotations

from typing import Any

import pytest
from joserfc.jwk import OKPKey
from pydantic import ValidationError

from asap_compliance.config import A2A_BRIDGE_PROFILE, A2ABridgeComplianceConfig

_AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
_CARD_KEY = OKPKey.generate_key("Ed25519", private=True)
_PUBLIC_JWK = {**_CARD_KEY.as_dict(private=False), "kid": "card-1"}


def _config_fields(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "base_url": "https://gateway.example",
        "expected_agent_urn": _AGENT_URN,
        "card_keys": {"card-1": _PUBLIC_JWK},
    }
    return {**base, **fields}


class TestA2ABridgeConfig:
    def test_profile_name_and_example_defaults(self) -> None:
        config = A2ABridgeComplianceConfig(**_config_fields())

        assert config.profile == A2A_BRIDGE_PROFILE == "a2a-bridge"
        assert (config.skill_id, config.allow_private_targets) == ("greet", False)
        assert config.allowed_arguments == {"name": "Ada", "language": "en"}
        assert config.constraint_violation_arguments == {"name": "Ada", "language": "fr"}
        assert (config.timeout_seconds, config.skip_checks) == (10.0, [])

    def test_each_configured_key_is_pinned_for_the_expected_urn_only(self) -> None:
        config = A2ABridgeComplianceConfig(**_config_fields())

        trust = config.card_trust()

        assert {kid: entry.agents for kid, entry in trust.pinned_keys.items()} == {
            "card-1": (_AGENT_URN,)
        }
        assert trust.jku_allowlist == {}

    @pytest.mark.parametrize(
        "base_url", ["http://127.0.0.1:8788", "https://127.0.0.1:8788", "https://localhost"]
    )
    def test_a_local_target_needs_the_switch(self, base_url: str) -> None:
        with pytest.raises(ValidationError, match="allow_private_targets"):
            A2ABridgeComplianceConfig(**_config_fields(base_url=base_url))

        allowed = _config_fields(base_url=base_url, allow_private_targets=True)
        assert A2ABridgeComplianceConfig(**allowed).base_url == base_url

    @pytest.mark.parametrize(
        "fields",
        [
            {"base_url": "http://gateway.example"},
            {"base_url": "http://8.8.8.8"},
            {"probe_tokens_url": "http://gateway.example/compliance/probe-tokens"},
        ],
    )
    def test_plain_http_to_a_public_host_is_refused_even_with_the_switch(
        self, fields: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError, match="loopback or private"):
            A2ABridgeComplianceConfig(**_config_fields(allow_private_targets=True, **fields))

    @pytest.mark.parametrize("switch", [False, True])
    def test_user_info_is_refused_and_never_echoed(self, switch: bool) -> None:
        fields = _config_fields(base_url="https://ops:hunter2@gateway.example")

        with pytest.raises(ValidationError) as excinfo:
            A2ABridgeComplianceConfig(**fields, allow_private_targets=switch)

        assert "hunter2" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "base_url", ["https://gateway.example/a2a", "https://gateway.example/?x=1"]
    )
    def test_the_base_url_is_an_origin(self, base_url: str) -> None:
        with pytest.raises(ValidationError, match="origin"):
            A2ABridgeComplianceConfig(**_config_fields(base_url=base_url))

    @pytest.mark.parametrize(
        "fields",
        [
            {"card_keys": {}},
            {"expected_agent_urn": "urn:asap:agent:Not-Lower"},
            {"card_keys": {"card-1": {"kty": "OKP"}}},
            {"profile": "mcp-auth-http"},
            {"timeout_seconds": 0},
            {"skip_check": ["card_well_known"]},
        ],
    )
    def test_a_bad_value_or_an_unknown_field_is_refused(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            A2ABridgeComplianceConfig(**_config_fields(**fields))

    def test_a_private_key_is_refused_and_never_echoed(self) -> None:
        private_jwk = {**_CARD_KEY.as_dict(private=True), "kid": "card-1"}

        with pytest.raises(ValidationError) as excinfo:
            A2ABridgeComplianceConfig(**_config_fields(card_keys={"card-1": private_jwk}))

        assert private_jwk["d"] not in str(excinfo.value)
