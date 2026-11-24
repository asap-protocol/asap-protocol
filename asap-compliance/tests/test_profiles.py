"""Tests for selecting a compliance profile by name."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from asap_compliance import (
    COMPLIANCE_PROFILES,
    McpAuthComplianceConfig,
    McpAuthResult,
    profile_config,
    validate_profile,
    validate_profile_async,
)
from asap_compliance.validators import A2A_BRIDGE_CHECK_IDS, MCP_AUTH_HTTP_CHECK_IDS

from .a2a_fake_gateway import AGENT_URN, BASE_URL, CARD_KID, CARD_PUBLIC_JWK, FakeA2AGateway
from .a2a_fake_gateway import PROBE_TOKENS_PATH as A2A_PROBE_TOKENS_PATH
from .mcp_http_fake_server import PROBE_TOKENS_PATH as MCP_PROBE_TOKENS_PATH
from .mcp_http_fake_server import FakeMcpAuthServer

_MCP_HTTP_FIELDS: dict[str, Any] = {
    "mcp_url": "http://mcp.test/mcp",
    "probe_tokens_url": f"http://mcp.test{MCP_PROBE_TOKENS_PATH}",
}
_A2A_BRIDGE_FIELDS: dict[str, Any] = {
    "base_url": BASE_URL,
    "expected_agent_urn": AGENT_URN,
    "card_keys": {CARD_KID: CARD_PUBLIC_JWK},
    "probe_tokens_url": f"{BASE_URL}{A2A_PROBE_TOKENS_PATH}",
}


def _fake_mcp_server() -> httpx.MockTransport:
    return httpx.MockTransport(FakeMcpAuthServer().handle)


def _fake_a2a_gateway() -> httpx.MockTransport:
    return httpx.MockTransport(FakeA2AGateway().handle)


_HTTP_PROFILES = [
    pytest.param("mcp-auth-http", _MCP_HTTP_FIELDS, _fake_mcp_server, MCP_AUTH_HTTP_CHECK_IDS),
    pytest.param("a2a-bridge", _A2A_BRIDGE_FIELDS, _fake_a2a_gateway, A2A_BRIDGE_CHECK_IDS),
]


class TestSelectAProfileByName:
    def test_the_three_profiles_are_known_by_name(self) -> None:
        assert COMPLIANCE_PROFILES == ("a2a-bridge", "mcp-auth-bridge", "mcp-auth-http")

    @pytest.mark.parametrize(("name", "fields", "fake_server", "check_ids"), _HTTP_PROFILES)
    async def test_selecting_an_http_profile_by_name_runs_it(
        self, name: str, fields: dict[str, Any], fake_server: Any, check_ids: tuple[str, ...]
    ) -> None:
        config = profile_config(name, fields)

        result = await validate_profile_async(config, http_transport=fake_server())

        assert (result.profile, result.passed) == (name, True)
        assert [check.name for check in result.checks] == list(check_ids)

    def test_the_sync_runner_runs_the_selected_profile(self) -> None:
        config = profile_config("a2a-bridge", _A2A_BRIDGE_FIELDS)

        result = validate_profile(config, http_transport=_fake_a2a_gateway())

        assert [check.name for check in result.checks] == list(A2A_BRIDGE_CHECK_IDS)

    def test_an_unknown_profile_fails_and_names_the_known_ones(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            profile_config("mcp-auth", {"mcp_url": "http://mcp.test/mcp"})

        message = str(excinfo.value)
        assert "unknown compliance profile 'mcp-auth'" in message
        assert "a2a-bridge, mcp-auth-bridge, mcp-auth-http" in message

    def test_fields_that_name_another_profile_are_refused(self) -> None:
        with pytest.raises(ValueError, match="'a2a-bridge', not 'mcp-auth-http'"):
            profile_config("mcp-auth-http", {**_MCP_HTTP_FIELDS, "profile": "a2a-bridge"})


class TestTheExistingProfile:
    def test_mcp_auth_bridge_by_name_is_its_default_config(self) -> None:
        assert profile_config("mcp-auth-bridge") == McpAuthComplianceConfig()

    async def test_the_stdio_profile_takes_no_http_options(self) -> None:
        with pytest.raises(ValueError, match="stdio subprocess"):
            await validate_profile_async(
                McpAuthComplianceConfig(), http_transport=_fake_mcp_server()
            )

    @pytest.mark.asap_compliance
    async def test_selecting_mcp_auth_bridge_runs_the_stdio_profile(self) -> None:
        result = await validate_profile_async(profile_config("mcp-auth-bridge"))

        assert isinstance(result, McpAuthResult)
        assert result.profile == "mcp-auth-bridge"
        assert result.passed, [check for check in result.checks if not check.passed]
