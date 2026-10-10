"""Unit tests for grant enforcement on protect_server (S2)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from asap.mcp.auth import protect_server
from asap.mcp.auth.errors import CAPABILITY_DENIED, CONSTRAINT_VIOLATION
from asap.auth.capabilities import CapabilityDefinition, CapabilityGrant, CapabilityRegistry
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
)
from asap.mcp.server import MCPServer
from tests.adapters.mcp.helpers import (
    build_grant_auth_config,
    error_text,
    tool_call_params,
)

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture

pytestmark = pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")


@pytest.mark.asyncio
async def test_denied_grant_returns_capability_denied(
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
) -> None:
    """Valid JWT without registry grant returns ``asap:capability_denied``."""
    register_capability("echo", description="Echo tool capability")
    token = mint_agent_jwt(capabilities=["echo"])
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)
    result = await protected._handle_tools_call(
        tool_call_params("echo", arguments={"message": "hi"}, jwt=token)
    )
    assert CAPABILITY_DENIED in error_text(result)


@pytest.mark.asyncio
async def test_jwt_capability_claim_mismatch_returns_capability_denied(
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """Active grant but JWT missing capability claim returns ``asap:capability_denied``."""
    register_capability("echo", description="Echo tool capability")
    grant_capability(agent_session.agent_id, "echo")
    token = mint_agent_jwt()
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)
    result = await protected._handle_tools_call(
        tool_call_params("echo", arguments={"message": "hi"}, jwt=token)
    )
    assert CAPABILITY_DENIED in error_text(result)


@pytest.mark.asyncio
async def test_grant_and_jwt_capability_succeeds(
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """Active grant and matching JWT capability claim invoke the handler."""
    register_capability("echo", description="Echo tool capability")
    grant_capability(agent_session.agent_id, "echo")
    token = mint_agent_jwt(capabilities=["echo"])
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)
    result = await protected._handle_tools_call(
        tool_call_params("echo", arguments={"message": "granted"}, jwt=token)
    )
    assert result["isError"] is False
    assert result["content"][0]["text"] == "granted"


@pytest.mark.asyncio
async def test_constraint_violation_returns_constraint_violation(
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """Arguments exceeding grant ``max`` constraint return ``asap:constraint_violation``."""
    register_capability("echo", description="Echo tool capability")
    grant_capability(
        agent_session.agent_id,
        "echo",
        constraints={"tokens": {"max": 100}},
    )
    token = mint_agent_jwt(capabilities=["echo"])
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)
    result = await protected._handle_tools_call(
        tool_call_params(
            "echo",
            arguments={"message": "hi", "tokens": 150},
            jwt=token,
        )
    )
    assert CONSTRAINT_VIOLATION in error_text(result)


@pytest.mark.asyncio
async def test_omitted_arguments_cannot_skip_constraints(
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """A constrained MCP grant stays denied when ``arguments`` is omitted or empty."""
    register_capability("echo", description="Echo tool capability")
    grant_capability(
        agent_session.agent_id,
        "echo",
        constraints={"tokens": {"max": 100}},
    )
    token = mint_agent_jwt(capabilities=["echo"])
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)

    omitted = await protected._handle_tools_call(
        {"name": "echo", "_meta": {"asap_agent_jwt": token}}
    )
    assert CONSTRAINT_VIOLATION in error_text(omitted)
    assert "tokens" in error_text(omitted)

    empty = await protected._handle_tools_call(tool_call_params("echo", arguments={}, jwt=token))
    assert CONSTRAINT_VIOLATION in error_text(empty)

    with pytest.raises(ValueError, match="Invalid params"):
        await protected._handle_tools_call(
            {"name": "echo", "arguments": None, "_meta": {"asap_agent_jwt": token}}
        )


@pytest.mark.asyncio
async def test_tool_capability_map_checks_resolved_capability_in_jwt(
    search_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """JWT ``capabilities`` claim must include the resolved capability name, not the tool name."""
    register_capability("web_search", description="Web search capability")
    grant_capability(agent_session.agent_id, "web_search")
    config = build_grant_auth_config(
        host_store,
        agent_store,
        capability_registry,
        tool_capability_map={"search": "web_search"},
    )
    protected = protect_server(search_mcp_server, config)

    wrong_claim = mint_agent_jwt(capabilities=["search"])
    denied = await protected._handle_tools_call(
        tool_call_params("search", arguments={"query": "hi"}, jwt=wrong_claim)
    )
    assert CAPABILITY_DENIED in error_text(denied)

    correct_claim = mint_agent_jwt(capabilities=["web_search"])
    allowed = await protected._handle_tools_call(
        tool_call_params("search", arguments={"query": "ok"}, jwt=correct_claim)
    )
    assert allowed["isError"] is False
    assert allowed["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_denied_grant_does_not_log_authorized(
    caplog: LogCaptureFixture,
    echo_mcp_server: MCPServer,
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
) -> None:
    """Failed grant enforcement must not emit ``mcp.tool.authorized``."""
    register_capability("echo", description="Echo tool capability")
    token = mint_agent_jwt(capabilities=["echo"])
    config = build_grant_auth_config(host_store, agent_store, capability_registry)
    protected = protect_server(echo_mcp_server, config)

    with caplog.at_level(logging.INFO):
        result = await protected._handle_tools_call(
            tool_call_params("echo", arguments={"message": "hi"}, jwt=token)
        )

    assert CAPABILITY_DENIED in error_text(result)
    assert "mcp.tool.authorized" not in caplog.text
