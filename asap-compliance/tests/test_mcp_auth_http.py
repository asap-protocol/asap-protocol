"""Tests for the ``mcp-auth-http`` profile: each check passes and fails."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap_compliance.config import MCP_AUTH_HTTP_PROFILE, McpAuthHttpComplianceConfig

_MCP_URL = "http://mcp.test/mcp"


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
