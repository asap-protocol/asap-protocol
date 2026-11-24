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

import asyncio
import base64
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from asap.auth.agent_jwt import AGENT_JWT_TYP, CAPABILITIES_CLAIM

from asap_compliance.config import McpAuthHttpComplianceConfig
from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.mcp_http_answers import (
    AUTH_REQUIRED_401,
    INVALID_TOKEN_401,
    has_bearer_challenge,
    tool_success_problem,
)
from asap_compliance.validators.mcp_http_transport import (
    McpHttpResponse,
    McpHttpTransport,
    McpHttpTransportError,
)

# Built by the harness; no server accepts it.
_MALFORMED_TOKEN = "asap-compliance-malformed-token"
# A JWT part this long belongs to one token; a shorter one could match by chance.
_MIN_ECHO_FRAGMENT = 16
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
    """One profile run: what to call, and every token and answer so far."""

    config: McpAuthHttpComplianceConfig
    transport: McpHttpTransport
    sent_tokens: list[str] = field(default_factory=list)
    responses: list[McpHttpResponse] = field(default_factory=list)

    async def call(
        self, tool: str, arguments: Mapping[str, Any], token: str | None
    ) -> McpHttpResponse:
        if token is not None:
            self.sent_tokens.append(token)
        response = await self.transport.call_tool(tool, arguments, bearer_token=token)
        self.responses.append(response)
        return response

    async def call_protected(self, token: str | None) -> McpHttpResponse:
        return await self.call(self.config.protected_tool, self.config.allowed_arguments, token)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _forged_agent_jwt(audience: str, capability: str) -> str:
    """A JWT-shaped Agent JWT for ``audience`` whose signature is random bytes.

    The header and claims look valid, so the token reaches the server's key and
    signature checks; a fresh ``jti`` and ``sub`` make each one unique.
    """
    now = int(time.time())
    header = {"alg": "EdDSA", "typ": AGENT_JWT_TYP}
    claims = {
        "iss": secrets.token_urlsafe(32),
        "sub": f"urn:asap:agent:compliance-probe-{secrets.token_hex(8)}",
        "aud": audience,
        "iat": now,
        "exp": now + 60,
        "jti": secrets.token_hex(16),
        CAPABILITIES_CLAIM: [capability],
    }
    signing_input = [_b64url(json.dumps(part).encode()) for part in (header, claims)]
    return ".".join([*signing_input, _b64url(secrets.token_bytes(64))])


async def _check_public_tool(run: _ProbeRun) -> str | None:
    """No token, the public tool."""
    config = run.config
    response = await run.call(config.public_tool, config.public_tool_arguments, None)
    return tool_success_problem(response)


async def _check_auth_required(run: _ProbeRun) -> str | None:
    """No token, the protected tool."""
    return AUTH_REQUIRED_401.problem(await run.call_protected(None))


async def _check_invalid_token(run: _ProbeRun) -> str | None:
    """A malformed token (not a JWT), the protected tool."""
    return INVALID_TOKEN_401.problem(await run.call_protected(_MALFORMED_TOKEN))


async def _check_bad_signature(run: _ProbeRun) -> str | None:
    """A JWT-shaped Agent JWT for the server's URL with a random signature."""
    token = _forged_agent_jwt(run.config.mcp_url, run.config.protected_tool)
    return INVALID_TOKEN_401.problem(await run.call_protected(token))


async def _check_www_authenticate_on_401(run: _ProbeRun) -> str | None:
    """Every HTTP 401 answer of the run so far."""
    unauthorized = [response for response in run.responses if response.status_code == 401]
    if not unauthorized:
        return "the run saw no HTTP 401"
    bare = [response for response in unauthorized if not has_bearer_challenge(response)]
    return f"{len(bare)} of {len(unauthorized)} HTTP 401 answers have none" if bare else None


async def _check_token_not_echoed(run: _ProbeRun) -> str | None:
    """Every answer of the run so far, against every token the run sent."""
    fragments = {
        fragment
        for token in run.sent_tokens
        for fragment in [token, *token.split(".")]
        if len(fragment) >= _MIN_ECHO_FRAGMENT
    }
    echoed = [
        response.request_id
        for response in run.responses
        for place in [response.body_text, *response.headers.values()]
        if any(fragment in place for fragment in fragments)
    ]
    return f"the answers to requests {sorted(set(echoed))} hold one" if echoed else None


_CheckRunner = Callable[[_ProbeRun], Awaitable[str | None]]

# (check ID, runner, what passing means), in the order the checks run.
_PROFILE_CHECKS: tuple[tuple[str, _CheckRunner, str], ...] = (
    ("public_tool", _check_public_tool, _EXPECT_TOOL_RESULT),
    ("auth_required", _check_auth_required, _EXPECT_AUTH_REQUIRED),
    ("invalid_token", _check_invalid_token, _EXPECT_INVALID_TOKEN),
    ("bad_signature", _check_bad_signature, _EXPECT_INVALID_TOKEN),
    # These two judge every answer of the run, so they run last.
    (
        "www_authenticate_on_401",
        _check_www_authenticate_on_401,
        "a WWW-Authenticate header with the Bearer scheme on every HTTP 401",
    ),
    ("token_not_echoed", _check_token_not_echoed, "no answer that holds a token the run sent"),
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


def validate_mcp_auth_http(
    config: McpAuthHttpComplianceConfig,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> McpAuthHttpResult:
    """Sync wrapper for :func:`validate_mcp_auth_http_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(validate_mcp_auth_http_async(config, http_transport=http_transport))
    raise RuntimeError(
        "Cannot call sync validate_mcp_auth_http from inside a running event loop. "
        "Use validate_mcp_auth_http_async."
    )
