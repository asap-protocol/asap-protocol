"""Tests for the ``mcp-auth-http`` profile: each check passes and fails."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urljoin

import httpx
import pytest
from pydantic import ValidationError

from asap_compliance.config import MCP_AUTH_HTTP_PROFILE, McpAuthHttpComplianceConfig
from asap_compliance.validators.mcp_auth_http import (
    MCP_AUTH_HTTP_CHECK_IDS,
    McpAuthHttpResult,
    validate_mcp_auth_http,
    validate_mcp_auth_http_async,
)
from asap_compliance.validators.probe_tokens import ProbeTokenError, ProbeTokenKind

from .mcp_http_fake_server import PROBE_TOKENS_PATH, FakeMcpAuthServer, Fault

_MCP_URL = "http://mcp.test/mcp"
_GRANT_CHECKS = {
    "wrong_capability",
    "constraint_violation",
    "external_wrong_capability",
    "external_constraint_violation",
}
_CREDENTIAL_CHECKS = {
    "valid_jwt",
    "replayed_jwt",
    "external_valid_token",
    "undeclared_tool",
    *_GRANT_CHECKS,
}


def _config(**fields: Any) -> McpAuthHttpComplianceConfig:
    probe_tokens_url = f"http://mcp.test{PROBE_TOKENS_PATH}"
    return McpAuthHttpComplianceConfig(
        mcp_url=_MCP_URL, probe_tokens_url=probe_tokens_url, **fields
    )


async def _run(server: FakeMcpAuthServer, **config_fields: Any) -> McpAuthHttpResult:
    transport = httpx.MockTransport(server.handle)
    return await validate_mcp_auth_http_async(_config(**config_fields), http_transport=transport)


class _NoProbeTokens:
    async def probe_token(self, kind: ProbeTokenKind) -> str:
        raise ProbeTokenError(f"no {kind.value!r} token in this test")


def _failed(result: McpAuthHttpResult) -> set[str]:
    return {check.name for check in result.checks if not check.passed}


def _json_part(part: str) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    return decoded


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
    """Token checks, each with the credential its case needs."""

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
            (Fault.INVALID_TOKEN_IN_TOOL_RESULT, "bad_signature"),
            (Fault.ACCEPTS_UNSIGNED_JWT, "bad_signature"),
            (Fault.NO_WWW_AUTHENTICATE, "www_authenticate_on_401"),
            (Fault.ECHOES_TOKEN_IN_BODY, "token_not_echoed"),
            (Fault.ECHOES_TOKEN_IN_HEADER, "token_not_echoed"),
            (Fault.REFUSES_VALID_TOKENS, "valid_jwt"),
            (Fault.ACCEPTS_REPLAY, "replayed_jwt"),
            (Fault.REFUSES_VALID_TOKENS, "external_valid_token"),
            (Fault.EXTERNAL_SINGLE_USE, "external_valid_token"),
            (Fault.RUNS_UNDECLARED_TOOL, "undeclared_tool"),
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

        http_checks = {"auth_required", "invalid_token", "bad_signature", "replayed_jwt"}
        assert {*http_checks, "undeclared_tool", "www_authenticate_on_401"} <= _failed(result)

    async def test_two_runs_in_a_row_pass_because_each_call_gets_a_fresh_token(self) -> None:
        server = FakeMcpAuthServer()

        assert (await _run(server)).passed
        assert (await _run(server)).passed

    async def test_a_passed_token_provider_is_used_and_its_errors_fail_only_its_checks(
        self,
    ) -> None:
        transport = httpx.MockTransport(FakeMcpAuthServer().handle)
        result = await validate_mcp_auth_http_async(
            _config(), token_provider=_NoProbeTokens(), http_transport=transport
        )

        assert _failed(result) == _CREDENTIAL_CHECKS
        assert all("in this test" in c.message for c in result.checks if not c.passed)

    async def test_a_run_with_no_token_source_is_refused(self) -> None:
        config = McpAuthHttpComplianceConfig(mcp_url=_MCP_URL)

        with pytest.raises(ValueError, match="probe_tokens_url"):
            await validate_mcp_auth_http_async(config)

    async def test_the_bad_signature_token_is_an_agent_jwt_for_the_server_audience(self) -> None:
        server = FakeMcpAuthServer()

        await _run(server)
        await _run(server)

        forged = [token for token in server.received_tokens if token.count(".") == 2]
        header, claims = (_json_part(part) for part in forged[0].split(".")[:2])
        assert (header["alg"], header["typ"]) == ("EdDSA", "agent+jwt")
        assert claims["aud"] == _MCP_URL
        assert claims["iat"] < claims["exp"]
        assert len(set(forged)) == len(forged) == 2

    async def test_no_check_message_holds_a_token_that_the_server_echoed(self) -> None:
        faults = (Fault.ECHOES_TOKEN_IN_BODY, Fault.ECHOES_TOKEN_IN_HEADER)
        server = FakeMcpAuthServer(*faults, Fault.INVALID_TOKEN_IN_TOOL_RESULT)

        result = await _run(server)

        messages = "\n".join(check.message for check in result.checks)
        parts = {part for token in server.received_tokens for part in [token, *token.split(".")]}
        assert len(server.received_tokens) >= 2
        assert not [part for part in parts if len(part) >= 16 and part in messages]

    async def test_a_skipped_check_passes_with_the_message_skipped(self) -> None:
        server = FakeMcpAuthServer(Fault.PUBLIC_TOOL_NEEDS_TOKEN)

        result = await _run(server, skip_checks=["public_tool"])

        public_tool = next(check for check in result.checks if check.name == "public_tool")
        assert (public_tool.passed, public_tool.message) == (True, "skipped")

    async def test_a_server_that_cannot_be_reached_fails_each_check_without_raising(self) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        result = await validate_mcp_auth_http_async(
            _config(), http_transport=httpx.MockTransport(unreachable)
        )

        call_checks = {"public_tool", "auth_required", "invalid_token", "bad_signature"}
        assert _failed(result) == {*call_checks, *_CREDENTIAL_CHECKS, "www_authenticate_on_401"}
        assert all("ConnectError" in c.message for c in result.checks if c.name in call_checks)

    def test_the_sync_wrapper_runs_the_profile(self) -> None:
        transport = httpx.MockTransport(FakeMcpAuthServer().handle)

        assert validate_mcp_auth_http(_config(), http_transport=transport).passed


class TestGrantLayerChecks:
    """Grant errors, in their layers (PRD MCP-TS-002)."""

    @pytest.mark.parametrize(
        ("fault", "check_id"),
        [
            (Fault.GRANTS_EVERYTHING, "wrong_capability"),
            (Fault.AGENT_DENIAL_AS_403, "wrong_capability"),
            (Fault.IGNORES_CONSTRAINTS, "constraint_violation"),
            (Fault.GRANTS_EVERYTHING, "external_wrong_capability"),
            (Fault.EXTERNAL_DENIAL_IN_TOOL_RESULT, "external_wrong_capability"),
            (Fault.IGNORES_CONSTRAINTS, "external_constraint_violation"),
        ],
    )
    async def test_a_server_with_the_fault_fails_the_check(
        self, fault: Fault, check_id: str
    ) -> None:
        result = await _run(FakeMcpAuthServer(fault))

        assert check_id in _failed(result)

    async def test_a_server_that_answers_200_to_everything_fails_every_grant_check(self) -> None:
        result = await _run(FakeMcpAuthServer(Fault.ANSWERS_200_TO_EVERYTHING))

        assert _failed(result).issuperset(_GRANT_CHECKS)

    async def test_the_constraint_checks_send_the_configured_arguments(self) -> None:
        result = await _run(
            FakeMcpAuthServer(), constraint_violation_arguments={"action": "deploy"}
        )

        assert _failed(result) == {"constraint_violation", "external_constraint_violation"}


@pytest.mark.mcp_http_live
class TestLiveExampleServer:
    """Against ``examples/mcp_auth_http`` started with ``--compliance`` (CI: live flows)."""

    async def test_the_profile_passes_twice_in_a_row(self, live_mcp_http_url: str) -> None:
        # The example serves its probe endpoint at the path that the fake server copies.
        config = McpAuthHttpComplianceConfig(
            mcp_url=live_mcp_http_url,
            probe_tokens_url=urljoin(live_mcp_http_url, PROBE_TOKENS_PATH),
        )

        for _ in range(2):
            result = await validate_mcp_auth_http_async(config)
            assert result.passed, [check for check in result.checks if not check.passed]
