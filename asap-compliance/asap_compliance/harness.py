"""Test harness for ASAP protocol compliance evaluation.

A compliance profile is selected by name: :func:`profile_config` builds its
config from its fields, and :func:`validate_profile` runs the profile of a config::

    config = profile_config("mcp-auth-http", {"mcp_url": url, "probe_tokens_url": probe_url})
    assert validate_profile(config).passed
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

import httpx

from asap_compliance.config import (
    A2A_BRIDGE_PROFILE,
    MCP_AUTH_BRIDGE_PROFILE,
    MCP_AUTH_HTTP_PROFILE,
    A2ABridgeComplianceConfig,
    ComplianceConfig,
    McpAuthComplianceConfig,
    McpAuthHttpComplianceConfig,
)
from asap_compliance.validators.a2a_bridge import (
    A2ABridgeResult,
    validate_a2a_bridge,
    validate_a2a_bridge_async,
)
from asap_compliance.validators.handshake import (
    HandshakeResult,
    validate_handshake,
    validate_handshake_async,
)
from asap_compliance.validators.mcp_auth import (
    McpAuthResult,
    validate_mcp_auth,
    validate_mcp_auth_async,
)
from asap_compliance.validators.mcp_auth_http import (
    McpAuthHttpResult,
    validate_mcp_auth_http,
    validate_mcp_auth_http_async,
)
from asap_compliance.validators.probe_tokens import ProbeTokenProvider
from asap_compliance.validators.schema import SchemaResult, validate_schema
from asap_compliance.validators.sla import SlaResult, validate_sla, validate_sla_async
from asap_compliance.validators.state import (
    StateResult,
    validate_state_machine,
    validate_state_machine_async,
)

ProfileConfig = McpAuthComplianceConfig | McpAuthHttpComplianceConfig | A2ABridgeComplianceConfig
ProfileResult = McpAuthResult | McpAuthHttpResult | A2ABridgeResult

_PROFILE_CONFIGS: Final[Mapping[str, type[ProfileConfig]]] = {
    A2A_BRIDGE_PROFILE: A2ABridgeComplianceConfig,
    MCP_AUTH_BRIDGE_PROFILE: McpAuthComplianceConfig,
    MCP_AUTH_HTTP_PROFILE: McpAuthHttpComplianceConfig,
}
COMPLIANCE_PROFILES: Final[tuple[str, ...]] = tuple(_PROFILE_CONFIGS)


def profile_config(profile: str, fields: Mapping[str, Any] | None = None) -> ProfileConfig:
    """Build the config of the profile named *profile* from its *fields*.

    Raises:
        ValueError: for a name that is not in ``COMPLIANCE_PROFILES``, or *fields*
            whose ``profile`` names another profile.
        pydantic.ValidationError: for fields that the profile's config refuses.
    """
    config_model = _PROFILE_CONFIGS.get(profile)
    if config_model is None:
        known = ", ".join(COMPLIANCE_PROFILES)
        raise ValueError(f"unknown compliance profile {profile!r}; expected one of {known}")
    values = dict(fields or {})
    named = values.setdefault("profile", profile)
    if named != profile:
        raise ValueError(f"the fields name the profile {named!r}, not {profile!r}")
    return config_model.model_validate(values)


async def validate_profile_async(
    config: ProfileConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> ProfileResult:
    """Run the profile that *config* belongs to.

    Args:
        config: A config from :func:`profile_config`, or one of the profile config models.
        token_provider: Fresh probe tokens for an HTTP profile. Default: the
            test-only probe endpoint at the config's ``probe_tokens_url``.
        http_transport: Optional httpx transport for an HTTP profile, for tests with
            a mock server.

    Raises:
        ValueError: for ``token_provider`` or ``http_transport`` with the
            ``mcp-auth-bridge`` profile, which drives a stdio subprocess.
    """
    if isinstance(config, McpAuthHttpComplianceConfig):
        return await validate_mcp_auth_http_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
    if isinstance(config, A2ABridgeComplianceConfig):
        return await validate_a2a_bridge_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
    if token_provider is not None or http_transport is not None:
        raise ValueError(
            f"the {MCP_AUTH_BRIDGE_PROFILE} profile drives a stdio subprocess; "
            "token_provider and http_transport are for the HTTP profiles only"
        )
    return await validate_mcp_auth_async(config)


def validate_profile(
    config: ProfileConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> ProfileResult:
    """Sync wrapper for :func:`validate_profile_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        profile_run = validate_profile_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
        return asyncio.run(profile_run)
    raise RuntimeError(
        "Cannot call sync validate_profile from inside a running event loop. "
        "Use validate_profile_async."
    )


__all__ = [
    "A2ABridgeComplianceConfig",
    "A2ABridgeResult",
    "COMPLIANCE_PROFILES",
    "ComplianceConfig",
    "HandshakeResult",
    "McpAuthComplianceConfig",
    "McpAuthHttpComplianceConfig",
    "McpAuthHttpResult",
    "McpAuthResult",
    "ProfileConfig",
    "ProfileResult",
    "SchemaResult",
    "SlaResult",
    "StateResult",
    "profile_config",
    "validate_a2a_bridge",
    "validate_a2a_bridge_async",
    "validate_handshake",
    "validate_handshake_async",
    "validate_mcp_auth",
    "validate_mcp_auth_async",
    "validate_mcp_auth_http",
    "validate_mcp_auth_http_async",
    "validate_profile",
    "validate_profile_async",
    "validate_schema",
    "validate_sla",
    "validate_sla_async",
    "validate_state_machine",
    "validate_state_machine_async",
]
