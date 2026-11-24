"""HTTP MCP auth compliance validation (profile ``mcp-auth-http``).

Runs against a remote Streamable HTTP MCP server protected by ``@asap-protocol/mcp-auth``,
over plain HTTP. Each check sends its own requests and judges each answer with
:mod:`asap_compliance.validators.mcp_http_answers`, so it passes only for the right code
in the layer of the ``@asap-protocol/mcp-auth`` README's error-code table. Each check
picks the credential of its case. Each token comes fresh from a
:class:`~asap_compliance.validators.probe_tokens.ProbeTokenProvider`, so a second run
passes too.

Example::

    config = McpAuthHttpComplianceConfig(
        mcp_url="http://127.0.0.1:8787/mcp",
        probe_tokens_url="http://127.0.0.1:8787/compliance/probe-tokens",
    )
    result = await validate_mcp_auth_http_async(config)
    assert result.passed, [check for check in result.checks if not check.passed]
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from asap.auth.agent_jwt import AGENT_JWT_TYP, CAPABILITIES_CLAIM
from asap.mcp.auth.errors import CAPABILITY_DENIED, CONSTRAINT_VIOLATION

from asap_compliance.config import McpAuthHttpComplianceConfig
from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.mcp_http_answers import (
    AUTH_REQUIRED_401,
    CAPABILITY_DENIED_403,
    INVALID_TOKEN_401,
    has_bearer_challenge,
    tool_error_problem,
    tool_success_problem,
)
from asap_compliance.validators.mcp_http_transport import McpHttpResponse, McpHttpTransport
from asap_compliance.validators.probe_tokens import ProbeTokenKind, ProbeTokenProvider
from asap_compliance.validators.profile_run import (
    ProfileCheck,
    endpoint_token_provider,
    run_profile_checks,
)

# Built by the harness; no server accepts it.
_MALFORMED_TOKEN = "asap-compliance-malformed-token"
_UNDECLARED_TOOL = "asap_compliance_undeclared_tool"
# A JWT part this long belongs to one token; a shorter one could match by chance.
_MIN_ECHO_FRAGMENT = 16
# What passing means, for the check messages.
_EXPECT_TOOL_RESULT = "HTTP 200 and a tool result"
_EXPECT_AUTH_REQUIRED = "HTTP 401, a Bearer challenge with no error, asap:auth_required"
_EXPECT_INVALID_TOKEN = (
    'HTTP 401, a Bearer challenge with error="invalid_token", asap:invalid_token'
)
_EXPECT_CAPABILITY_DENIED_403 = (
    'HTTP 403, a Bearer challenge with error="insufficient_scope", asap:capability_denied'
)
_EXPECT_CAPABILITY_DENIED_RESULT = "HTTP 200 and a tool error result asap:capability_denied"
_EXPECT_CONSTRAINT_VIOLATION = "HTTP 200 and a tool error result asap:constraint_violation"


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
    """One profile run: what to call, where tokens come from, every token and answer so far."""

    config: McpAuthHttpComplianceConfig
    transport: McpHttpTransport
    tokens: ProbeTokenProvider
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


async def _check_valid_jwt(run: _ProbeRun) -> str | None:
    """A fresh Agent JWT with the grant, allowed arguments."""
    token = await run.tokens.probe_token(ProbeTokenKind.AGENT_JWT)
    return tool_success_problem(await run.call_protected(token))


async def _check_replayed_jwt(run: _ProbeRun) -> str | None:
    """One fresh Agent JWT, sent twice: an Agent JWT is single-use."""
    return await _second_use_problem(run, ProbeTokenKind.AGENT_JWT, INVALID_TOKEN_401.problem)


async def _check_external_valid_token(run: _ProbeRun) -> str | None:
    """One fresh access token, sent twice: MCP sends it on every request, so it is reusable."""
    return await _second_use_problem(run, ProbeTokenKind.EXTERNAL, tool_success_problem)


async def _second_use_problem(
    run: _ProbeRun,
    kind: ProbeTokenKind,
    second_use_problem: Callable[[McpHttpResponse], str | None],
) -> str | None:
    token = await run.tokens.probe_token(kind)
    first_use = tool_success_problem(await run.call_protected(token))
    if first_use is not None:
        return f"on the first use, {first_use}"
    second_use = second_use_problem(await run.call_protected(token))
    return None if second_use is None else f"on the second use, {second_use}"


async def _check_wrong_capability(run: _ProbeRun) -> str | None:
    """A fresh Agent JWT without the grant: on this path a grant error is a tool result."""
    token = await run.tokens.probe_token(ProbeTokenKind.AGENT_JWT_OUT_OF_GRANT)
    return tool_error_problem(await run.call_protected(token), CAPABILITY_DENIED)


async def _check_constraint_violation(run: _ProbeRun) -> str | None:
    """A fresh Agent JWT with the grant, arguments that break the grant's constraint."""
    return await _constraint_violation_problem(run, ProbeTokenKind.AGENT_JWT)


async def _check_external_wrong_capability(run: _ProbeRun) -> str | None:
    """A fresh access token whose scopes give no grant: HTTP 403 on this path (MCP-TS-002)."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL_OUT_OF_GRANT)
    return CAPABILITY_DENIED_403.problem(await run.call_protected(token))


async def _check_external_constraint_violation(run: _ProbeRun) -> str | None:
    """A fresh access token with the grant, arguments that break the constraint."""
    return await _constraint_violation_problem(run, ProbeTokenKind.EXTERNAL)


async def _constraint_violation_problem(run: _ProbeRun, kind: ProbeTokenKind) -> str | None:
    # A constraint depends on the arguments, so it stays a tool result on both paths.
    token = await run.tokens.probe_token(kind)
    config = run.config
    response = await run.call(config.protected_tool, config.constraint_violation_arguments, token)
    return tool_error_problem(response, CONSTRAINT_VIOLATION)


async def _check_undeclared_tool(run: _ProbeRun) -> str | None:
    """A fresh Agent JWT, a tool that the server does not declare (deny by default)."""
    token = await run.tokens.probe_token(ProbeTokenKind.AGENT_JWT)
    return CAPABILITY_DENIED_403.problem(await run.call(_UNDECLARED_TOOL, {}, token))


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


# The checks, in the order they run.
_PROFILE_CHECKS: tuple[ProfileCheck[_ProbeRun], ...] = (
    ("public_tool", _check_public_tool, _EXPECT_TOOL_RESULT),
    ("auth_required", _check_auth_required, _EXPECT_AUTH_REQUIRED),
    ("invalid_token", _check_invalid_token, _EXPECT_INVALID_TOKEN),
    ("bad_signature", _check_bad_signature, _EXPECT_INVALID_TOKEN),
    ("valid_jwt", _check_valid_jwt, _EXPECT_TOOL_RESULT),
    (
        "replayed_jwt",
        _check_replayed_jwt,
        f"{_EXPECT_TOOL_RESULT}, then on the second use {_EXPECT_INVALID_TOKEN}",
    ),
    ("wrong_capability", _check_wrong_capability, _EXPECT_CAPABILITY_DENIED_RESULT),
    ("constraint_violation", _check_constraint_violation, _EXPECT_CONSTRAINT_VIOLATION),
    ("external_valid_token", _check_external_valid_token, f"{_EXPECT_TOOL_RESULT}, twice"),
    (
        "external_wrong_capability",
        _check_external_wrong_capability,
        _EXPECT_CAPABILITY_DENIED_403,
    ),
    (
        "external_constraint_violation",
        _check_external_constraint_violation,
        _EXPECT_CONSTRAINT_VIOLATION,
    ),
    ("undeclared_tool", _check_undeclared_tool, _EXPECT_CAPABILITY_DENIED_403),
    # These two judge every answer of the run, so they run last.
    (
        "www_authenticate_on_401",
        _check_www_authenticate_on_401,
        "a WWW-Authenticate header with the Bearer scheme on every HTTP 401",
    ),
    ("token_not_echoed", _check_token_not_echoed, "no answer that holds a token the run sent"),
)

MCP_AUTH_HTTP_CHECK_IDS: tuple[str, ...] = tuple(check_id for check_id, _, _ in _PROFILE_CHECKS)


async def validate_mcp_auth_http_async(
    config: McpAuthHttpComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> McpAuthHttpResult:
    """Run the ``mcp-auth-http`` profile against a remote HTTP MCP server.

    Args:
        config: Profile configuration: the MCP URL, and the tools and arguments to call.
        token_provider: Source of fresh probe tokens, asked once per request that needs
            one. Default: the server's test-only probe endpoint at ``probe_tokens_url``.
        http_transport: Optional httpx transport, for tests with a mock server.

    Returns:
        One ``CheckResult`` per ID in ``MCP_AUTH_HTTP_CHECK_IDS``, in that order. A check
        that gets no full HTTP answer, or no token, fails, and the run goes on.

    Raises:
        ValueError: if there is neither a ``token_provider`` nor a ``probe_tokens_url``.
    """
    if token_provider is None:
        token_provider = endpoint_token_provider(
            config.profile,
            config.probe_tokens_url,
            timeout_seconds=config.timeout_seconds,
            http_transport=http_transport,
        )
    transport = McpHttpTransport(
        config.mcp_url, timeout_seconds=config.timeout_seconds, http_transport=http_transport
    )
    run = _ProbeRun(config, transport, token_provider)
    checks = await run_profile_checks(run, _PROFILE_CHECKS, skip_checks=config.skip_checks)
    return McpAuthHttpResult(profile=config.profile, checks=checks)


def validate_mcp_auth_http(
    config: McpAuthHttpComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> McpAuthHttpResult:
    """Sync wrapper for :func:`validate_mcp_auth_http_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        profile_run = validate_mcp_auth_http_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
        return asyncio.run(profile_run)
    raise RuntimeError(
        "Cannot call sync validate_mcp_auth_http from inside a running event loop. "
        "Use validate_mcp_auth_http_async."
    )
