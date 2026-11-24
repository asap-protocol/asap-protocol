"""Configuration for ASAP compliance harness."""

from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from typing import Any, Final, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from asap.adapters.a2a import A2ATrustConfig, PinnedCardKey
from asap.mcp.auth.config import MCP_COMPLIANCE_ENV_VAR
from asap.utils.guarded_fetch import GuardedFetchError, is_public_ip_address, validate_guarded_url

# Requires asap-protocol>=2.5.0 (MCP Auth Bridge adapter ships in v2.5.0).
MCP_AUTH_BRIDGE_PROFILE = "mcp-auth-bridge"
# A remote HTTP MCP server protected by @asap-protocol/mcp-auth (v2.6.0).
MCP_AUTH_HTTP_PROFILE: Final = "mcp-auth-http"
# A remote A2A v1.0 agent served by an ASAP gateway (v2.6.0).
A2A_BRIDGE_PROFILE: Final = "a2a-bridge"
COMPLIANCE_ENV_VAR = MCP_COMPLIANCE_ENV_VAR
_HTTP_URL_PATTERN = r"^https?://"


def find_repo_root() -> Path:
    """Locate the asap-protocol repository root from this package."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "examples" / "mcp_auth_bridge" / "server.py").is_file():
            return parent
    raise FileNotFoundError(
        "Could not locate asap-protocol repo root (expected examples/mcp_auth_bridge/server.py)"
    )


def default_mcp_auth_server_command() -> list[str]:
    """Default stdio subprocess command for the MCP Auth Bridge example."""
    override = os.environ.get("ASAP_MCP_SERVER_COMMAND")
    if override:
        return override.split()
    script = find_repo_root() / "examples" / "mcp_auth_bridge" / "server.py"
    return ["uv", "run", "python", str(script)]


def default_mcp_auth_manifest_fixture() -> Path:
    """Default manifest fixture JSON for MCP-DISC-003 alignment checks."""
    return (
        find_repo_root()
        / "asap-compliance"
        / "tests"
        / "fixtures"
        / "mcp_auth_bridge_manifest.json"
    )


class McpAuthComplianceConfig(BaseModel):
    """Configuration for the ``mcp-auth-bridge`` stdio MCP compliance profile."""

    profile: str = Field(
        default=MCP_AUTH_BRIDGE_PROFILE,
        description="Compliance profile name (release gate for stdio MCP)",
    )
    server_command: list[str] = Field(
        default_factory=default_mcp_auth_server_command,
        description="Subprocess command for the MCP server under test (stdio JSON-RPC)",
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Per-request timeout when driving the MCP subprocess",
    )
    protected_tool: str = Field(
        default="secure_action",
        description="Protected MCP tool name (requires Agent JWT)",
    )
    public_tool: str = Field(
        default="echo",
        description="Public MCP tool name (no JWT required)",
    )
    manifest_fixture_path: Path | None = Field(
        default_factory=default_mcp_auth_manifest_fixture,
        description="Fixture manifest JSON when manifest_url is unavailable",
    )
    manifest_url: str | None = Field(
        default=None,
        description="Optional live manifest URL for MCP-DISC-003 alignment",
    )
    skip_checks: list[str] = Field(
        default_factory=list,
        description="Check names to skip (e.g. 'manifest_alignment')",
    )
    compliance_env: bool = Field(
        default=True,
        description="Set ASAP_MCP_COMPLIANCE=1 on subprocess for probe JWT emission",
    )
    allowed_binaries: frozenset[str] | None = Field(
        default_factory=lambda: frozenset({"uv", "python", Path(sys.executable).name}),
        description="Allowed server_command binaries for MCPClient validation",
    )


class McpAuthHttpComplianceConfig(BaseModel):
    """Configuration for the ``mcp-auth-http`` profile (remote HTTP MCP server).

    The tool names and arguments default to ``examples/mcp_auth_http``.

    Example:
        >>> McpAuthHttpComplianceConfig(mcp_url="http://127.0.0.1:8787/mcp").public_tool
        'echo'
    """

    # A misspelled field would silently drop a setting such as a skipped check.
    model_config = ConfigDict(extra="forbid")

    profile: Literal["mcp-auth-http"] = Field(
        default=MCP_AUTH_HTTP_PROFILE,
        description="Compliance profile name",
    )
    mcp_url: str = Field(
        ...,
        pattern=_HTTP_URL_PATTERN,
        description="MCP endpoint URL of the server under test",
    )
    probe_tokens_url: str | None = Field(
        default=None,
        pattern=_HTTP_URL_PATTERN,
        description="Test-only probe endpoint (POST) for fresh tokens, if no provider is passed",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Deadline for each HTTP answer",
    )
    protected_tool: str = Field(
        default="secure_action",
        description="Tool that needs a token and a grant",
    )
    allowed_arguments: dict[str, Any] = Field(
        default_factory=lambda: {"action": "deploy"},
        description="Arguments of a protected call that the grant allows",
    )
    constraint_violation_arguments: dict[str, Any] = Field(
        default_factory=lambda: {"action": "forbidden-action"},
        description="Arguments of a protected call that break the grant's constraint",
    )
    public_tool: str = Field(
        default="echo",
        description="Tool that the server serves with no token",
    )
    public_tool_arguments: dict[str, Any] = Field(
        default_factory=lambda: {"message": "asap-compliance"},
        description="Arguments of the public tool call",
    )
    skip_checks: list[str] = Field(
        default_factory=list,
        description="Check IDs to skip; a skipped check passes with the message 'skipped'",
    )


class A2ABridgeComplianceConfig(BaseModel):
    """Configuration for the ``a2a-bridge`` profile: a remote A2A v1.0 agent behind an ASAP gateway.

    The card trust comes from this config only: ``expected_agent_urn`` and
    ``card_keys`` (``kid`` -> public JWK). Each key is pinned for that URN alone
    (:meth:`card_trust`); the profile never reads a key or a URN from the target.
    ``allow_private_targets`` is for local and CI runs only. The skill and its
    arguments default to ``examples/a2a_gateway``.

    Example:
        >>> A2ABridgeComplianceConfig(
        ...     base_url="http://127.0.0.1:8788", allow_private_targets=True,
        ...     expected_agent_urn="urn:asap:agent:a2a-gateway-demo",
        ...     card_keys={"gateway-card-1": card_public_jwk},
        ... ).skill_id  # doctest: +SKIP
        'greet'
    """

    # Unknown fields are refused; an error never echoes input (a key may be private).
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    profile: Literal["a2a-bridge"] = Field(default=A2A_BRIDGE_PROFILE)
    base_url: str = Field(..., description="Origin of the gateway under test")
    expected_agent_urn: str = Field(..., description="Agent URN that the card must name")
    card_keys: dict[str, dict[str, Any]] = Field(
        ..., min_length=1, description="kid -> public JWK of each key that may sign the card"
    )
    probe_tokens_url: str | None = Field(
        default=None, description="Test-only probe endpoint (POST) for fresh tokens"
    )
    allow_private_targets: bool = Field(
        default=False, description="Allow loopback or private targets; local and CI runs only"
    )
    timeout_seconds: float = Field(default=10.0, gt=0, description="Deadline for each answer")
    skill_id: str = Field(default="greet", min_length=1, description="Skill that the calls select")
    allowed_arguments: dict[str, Any] = Field(
        default_factory=lambda: {"name": "Ada", "language": "en"},
        description="Arguments that the grant allows",
    )
    constraint_violation_arguments: dict[str, Any] = Field(
        default_factory=lambda: {"name": "Ada", "language": "fr"},
        description="Arguments that break the grant's constraint",
    )
    skip_checks: list[str] = Field(
        default_factory=list,
        description="Check IDs to skip; a skipped check passes with the message 'skipped'",
    )

    @model_validator(mode="after")
    def _check_targets_and_trust(self) -> Self:
        _check_target_url("base_url", self.base_url, self.allow_private_targets)
        parsed = httpx.URL(self.base_url)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an origin (scheme, host, and port only)")
        if self.probe_tokens_url is not None:
            _check_target_url("probe_tokens_url", self.probe_tokens_url, self.allow_private_targets)
        self.card_trust()
        return self

    def card_trust(self) -> A2ATrustConfig:
        """Pin each configured key for ``expected_agent_urn`` only, with no ``jku`` allowlist."""
        scope = (self.expected_agent_urn,)
        pinned = {kid: PinnedCardKey(jwk=jwk, agents=scope) for kid, jwk in self.card_keys.items()}
        return A2ATrustConfig(pinned_keys=pinned)


def _check_target_url(field_name: str, url: str, allow_private_targets: bool) -> None:
    """Refuse a private target unless allowed; a message never repeats the URL."""
    if not allow_private_targets:
        try:
            validate_guarded_url(url)
        except GuardedFetchError as exc:
            raise ValueError(
                f"{field_name} is refused ({exc.code}): it must be https to a public host; "
                "set allow_private_targets=True for a local or CI run"
            ) from None
        return
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError(f"{field_name} is not a URL") from None
    if parsed.userinfo:
        raise ValueError(f"{field_name} holds user info")
    local_http = parsed.scheme == "http" and _is_private_host(parsed.host)
    if parsed.host and (parsed.scheme == "https" or local_http):
        return
    raise ValueError(f"{field_name} must be https, or http to a loopback or private address")


def _is_private_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return not is_public_ip_address(str(ipaddress.ip_address(host)))
    except ValueError:
        return False


class ComplianceConfig(BaseModel):
    agent_url: str = Field(..., description="Base URL of the agent under test")
    timeout_seconds: float = Field(default=30.0, gt=0, description="HTTP timeout in seconds")
    test_categories: list[str] = Field(
        default=["handshake", "schema", "state"],
        description="Test categories to run",
    )
    sla_skill_id: str = Field(
        default="echo",
        description="Skill ID for SLA and state validation (agent must implement this skill)",
    )
    skip_checks: list[str] = Field(
        default_factory=list,
        description="Check names to skip (e.g. 'sla' to skip SLA timeout check)",
    )
