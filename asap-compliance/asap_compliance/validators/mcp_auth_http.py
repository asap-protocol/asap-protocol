"""HTTP MCP auth compliance validation (profile ``mcp-auth-http``).

Runs against a remote Streamable HTTP MCP server protected by ``@asap-protocol/mcp-auth``,
over plain HTTP. Each check sends its own requests and judges each answer with
:mod:`asap_compliance.validators.mcp_http_answers`, so it passes only for the right code
in the layer of the ``@asap-protocol/mcp-auth`` README's error-code table. Each check
picks the credential of its case.

Example::

    config = McpAuthHttpComplianceConfig(mcp_url="http://127.0.0.1:8787/mcp")
    result = await validate_mcp_auth_http_async(config)
    assert result.passed, [check for check in result.checks if not check.passed]
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from asap_compliance.config import McpAuthHttpComplianceConfig
from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.mcp_http_answers import (
    AUTH_REQUIRED_401,
    INVALID_TOKEN_401,
    tool_success_problem,
)
from asap_compliance.validators.mcp_http_transport import (
    McpHttpResponse,
    McpHttpTransport,
    McpHttpTransportError,
)

# Built by the harness; no server accepts it.
_MALFORMED_TOKEN = "asap-compliance-malformed-token"
# What passing means, for the check messages.
_EXPECT_TOOL_RESULT = "HTTP 200 and a tool result"
_EXPECT_AUTH_REQUIRED = "HTTP 401, a Bearer challenge with no error, asap:auth_required"
_EXPECT_INVALID_TOKEN = (
    'HTTP 401, a Bearer challenge with error="invalid_token", asap:invalid_token'
)


@dataclass
class McpAuthHttpResult:
    """Summary for the ``mcp-auth-http`` compliance profile: one entry per check ID."""

    profile: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass
class _ProbeRun:
    """One profile run: what to call, and how."""

    config: McpAuthHttpComplianceConfig
    transport: McpHttpTransport

    async def call_protected(self, token: str | None) -> McpHttpResponse:
        config = self.config
        return await self.transport.call_tool(
            config.protected_tool, config.allowed_arguments, bearer_token=token
        )


async def _check_public_tool(run: _ProbeRun) -> str | None:
    """No token, the public tool."""
    config = run.config
    response = await run.transport.call_tool(config.public_tool, config.public_tool_arguments)
    return tool_success_problem(response)


async def _check_auth_required(run: _ProbeRun) -> str | None:
    """No token, the protected tool."""
    return AUTH_REQUIRED_401.problem(await run.call_protected(None))


async def _check_invalid_token(run: _ProbeRun) -> str | None:
    """A malformed token (not a JWT), the protected tool."""
    return INVALID_TOKEN_401.problem(await run.call_protected(_MALFORMED_TOKEN))


_CheckRunner = Callable[[_ProbeRun], Awaitable[str | None]]

# (check ID, runner, what passing means), in the order the checks run.
_PROFILE_CHECKS: tuple[tuple[str, _CheckRunner, str], ...] = (
    ("public_tool", _check_public_tool, _EXPECT_TOOL_RESULT),
    ("auth_required", _check_auth_required, _EXPECT_AUTH_REQUIRED),
    ("invalid_token", _check_invalid_token, _EXPECT_INVALID_TOKEN),
)

MCP_AUTH_HTTP_CHECK_IDS: tuple[str, ...] = tuple(check_id for check_id, _, _ in _PROFILE_CHECKS)


async def _run_check(
    run: _ProbeRun, check_id: str, runner: _CheckRunner, passing: str
) -> CheckResult:
    if check_id in run.config.skip_checks:
        return CheckResult(check_id, True, "skipped")
    try:
        problem = await runner(run)
    except McpHttpTransportError as exc:
        problem = str(exc)
    if problem is None:
        return CheckResult(check_id, True, passing)
    return CheckResult(check_id, False, f"expected {passing}; {problem}")


async def validate_mcp_auth_http_async(
    config: McpAuthHttpComplianceConfig,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> McpAuthHttpResult:
    """Run the ``mcp-auth-http`` profile against a remote HTTP MCP server.

    Args:
        config: Profile configuration: the MCP URL, and the tools and arguments to call.
        http_transport: Optional httpx transport, for tests with a mock server.

    Returns:
        One ``CheckResult`` per ID in ``MCP_AUTH_HTTP_CHECK_IDS``, in that order. A check
        that gets no full HTTP answer fails, and the run goes on.
    """
    transport = McpHttpTransport(
        config.mcp_url, timeout_seconds=config.timeout_seconds, http_transport=http_transport
    )
    run = _ProbeRun(config, transport)
    checks = [await _run_check(run, *check) for check in _PROFILE_CHECKS]
    return McpAuthHttpResult(profile=config.profile, checks=checks)
