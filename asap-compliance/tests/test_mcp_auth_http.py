"""Tests for the ``mcp-auth-http`` profile: each check passes and fails."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from asap_compliance.config import MCP_AUTH_HTTP_PROFILE, McpAuthHttpComplianceConfig
from asap_compliance.validators.mcp_auth_http import (
    MCP_AUTH_HTTP_CHECK_IDS,
    McpAuthHttpResult,
    validate_mcp_auth_http_async,
)

from .mcp_http_fake_server import FakeMcpAuthServer, Fault

_MCP_URL = "http://mcp.test/mcp"


async def _run(server: FakeMcpAuthServer, **config_fields: Any) -> McpAuthHttpResult:
    config = McpAuthHttpComplianceConfig(mcp_url=_MCP_URL, **config_fields)
    transport = httpx.MockTransport(server.handle)
    return await validate_mcp_auth_http_async(config, http_transport=transport)


def _failed(result: McpAuthHttpResult) -> set[str]:
    return {check.name for check in result.checks if not check.passed}


class TestMcpAuthHttpConfig:
    def test_profile_name_and_example_defaults(self) -> None:
        config = McpAuthHttpComplianceConfig(mcp_url=_MCP_URL)

        assert config.profile == MCP_AUTH_HTTP_PROFILE == "mcp-auth-http"
        assert (config.protected_tool, config.public_tool) == ("secure_action", "echo")
        assert config.allowed_arguments == {"action": "deploy"}
        assert config.skip_checks == []

    def test_each_config_gets_its_own_argument_dicts(self) -> None:
        first = McpAuthHttpComplianceConfig(mcp_url=_MCP_URL)
        first.allowed_arguments["action"] = "changed"

        assert McpAuthHttpComplianceConfig(mcp_url=_MCP_URL).allowed_arguments["action"] == "deploy"

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"mcp_url": "ftp://mcp.test/mcp"},
            {"mcp_url": _MCP_URL, "timeout_seconds": 0},
            {"mcp_url": _MCP_URL, "profile": "mcp-auth-bridge"},
            {"mcp_url": _MCP_URL, "skip_check": ["public_tool"]},
        ],
    )
    def test_a_missing_or_bad_value_or_an_unknown_field_is_refused(
        self, fields: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError):
            McpAuthHttpComplianceConfig(**fields)


class TestTokenLayerChecks:
    """Checks whose credential the harness builds itself."""

    async def test_a_server_that_follows_the_s6_table_passes_every_check(self) -> None:
        result = await _run(FakeMcpAuthServer())

        assert result.passed, [check for check in result.checks if not check.passed]
        assert result.profile == "mcp-auth-http"
        assert [check.name for check in result.checks] == list(MCP_AUTH_HTTP_CHECK_IDS)

    @pytest.mark.parametrize(
        ("fault", "check_id"),
        [
            (Fault.PUBLIC_TOOL_NEEDS_TOKEN, "public_tool"),
            (Fault.AUTH_REQUIRED_IN_TOOL_RESULT, "auth_required"),
            (Fault.INVALID_TOKEN_IN_TOOL_RESULT, "invalid_token"),
        ],
    )
    async def test_a_server_with_the_fault_fails_the_check(
        self, fault: Fault, check_id: str
    ) -> None:
        result = await _run(FakeMcpAuthServer(fault))

        assert check_id in _failed(result)
        assert not result.passed

    async def test_a_server_that_answers_200_to_everything_fails_the_http_checks(self) -> None:
        result = await _run(FakeMcpAuthServer(Fault.ANSWERS_200_TO_EVERYTHING))

        assert {"auth_required", "invalid_token"} <= _failed(result)

    async def test_a_skipped_check_passes_with_the_message_skipped(self) -> None:
        server = FakeMcpAuthServer(Fault.PUBLIC_TOOL_NEEDS_TOKEN)

        result = await _run(server, skip_checks=["public_tool"])

        public_tool = next(check for check in result.checks if check.name == "public_tool")
        assert (public_tool.passed, public_tool.message) == (True, "skipped")

    async def test_a_server_that_cannot_be_reached_fails_each_check_without_raising(self) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        config = McpAuthHttpComplianceConfig(mcp_url=_MCP_URL)
        result = await validate_mcp_auth_http_async(
            config, http_transport=httpx.MockTransport(unreachable)
        )

        assert _failed(result) == {"public_tool", "auth_required", "invalid_token"}
        assert all("ConnectError" in check.message for check in result.checks)
