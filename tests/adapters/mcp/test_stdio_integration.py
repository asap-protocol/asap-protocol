"""End-to-end stdio integration tests for protected MCPServer.

Exercises the full JSON-RPC stdio path (initialize → tools/call) against a
``protect_server``-wrapped server using runtime-minted Agent JWTs in
``params._meta.asap_agent_jwt``. Headless, no network, no committed secrets.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from asap.mcp.auth.errors import AUTH_REQUIRED, CAPABILITY_DENIED
from asap.mcp.auth import MCPAuthConfig, protect_server
from asap.auth.capabilities import CapabilityDefinition, CapabilityGrant, CapabilityRegistry
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
)
from asap.mcp.client import MCPClient
from asap.mcp.server import MCPServer
from tests.adapters.mcp.conftest import MCP_TEST_AUDIENCE
from tests.adapters.mcp.helpers import tool_call_params

pytestmark = pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_SERVER = _REPO_ROOT / "examples" / "mcp_auth_bridge" / "server.py"

_SECURE_ACTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"action": {"type": "string"}},
    "additionalProperties": False,
}

_ECHO_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "additionalProperties": False,
}


def _build_protected_secure_action_server(
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    register_capability: Callable[..., CapabilityDefinition],
) -> MCPServer:
    """Protected server with public ``echo`` and gated ``secure_action`` tools."""
    register_capability("secure_action", description="Protected MCP action")
    base = MCPServer(name="mcp-stdio-integration", version="0.1.0")
    base.register_tool(
        "echo",
        lambda message="": message,
        _ECHO_INPUT_SCHEMA,
        description="Public echo (no JWT)",
    )
    base.register_tool(
        "secure_action",
        lambda action="": f"executed: {action}",
        _SECURE_ACTION_INPUT_SCHEMA,
        description="Protected action (JWT + grant)",
    )
    config = MCPAuthConfig(
        host_store=host_store,
        agent_store=agent_store,
        capability_registry=capability_registry,
        public_tools=frozenset({"echo"}),
        enforce_grants=True,
        expected_audience=MCP_TEST_AUDIENCE,
    )
    return protect_server(base, config)


async def _stdio_tools_call(
    server: MCPServer,
    params: dict[str, Any],
    *,
    request_id: int = 3,
) -> dict[str, Any]:
    """Drive initialize handshake and one ``tools/call`` over injected stdio."""
    lines = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2025-11-25","capabilities":{},'
        '"clientInfo":{"name":"stdio-test","version":"0"}}}\n',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": params,
            }
        )
        + "\n",
        "\n",
    ]
    stdin = io.StringIO("".join(lines))
    stdout = io.StringIO()
    await server.run_stdio(stdin=stdin, stdout=stdout)
    out_lines = [json.loads(ln) for ln in stdout.getvalue().strip().split("\n") if ln.strip()]
    assert out_lines, "expected at least one JSON-RPC response on stdout"
    return out_lines[-1]


def _call_tool_result_text(response: dict[str, Any]) -> str:
    """Extract text from a ``tools/call`` JSON-RPC result payload."""
    result = response.get("result", {})
    content = result.get("content", [])
    if not content:
        return ""
    return str(content[0].get("text", ""))


@pytest.mark.asyncio
async def test_stdio_secure_action_succeeds_with_jwt_and_grant(
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """Valid JWT + active grant on ``secure_action`` succeeds over stdio JSON-RPC."""
    server = _build_protected_secure_action_server(
        host_store,
        agent_store,
        capability_registry,
        register_capability,
    )
    grant_capability(agent_session.agent_id, "secure_action")
    token = mint_agent_jwt(capabilities=["secure_action"])

    response = await _stdio_tools_call(
        server,
        tool_call_params("secure_action", arguments={"action": "stdio-ok"}, jwt=token),
    )

    assert "result" in response
    assert response["result"].get("isError") is not True
    assert _call_tool_result_text(response) == "executed: stdio-ok"


@pytest.mark.asyncio
async def test_stdio_secure_action_denied_without_jwt(
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    register_capability: Callable[..., CapabilityDefinition],
) -> None:
    """Protected ``secure_action`` without JWT returns ``asap:auth_required`` over stdio."""
    server = _build_protected_secure_action_server(
        host_store,
        agent_store,
        capability_registry,
        register_capability,
    )

    response = await _stdio_tools_call(
        server,
        tool_call_params("secure_action", arguments={"action": "denied"}),
    )

    assert "result" in response
    assert response["result"]["isError"] is True
    assert AUTH_REQUIRED in _call_tool_result_text(response)


@pytest.mark.asyncio
async def test_stdio_secure_action_denied_wrong_capability_in_jwt(
    host_store: InMemoryHostStore,
    agent_store: InMemoryAgentStore,
    capability_registry: CapabilityRegistry,
    host_identity: HostIdentity,
    agent_session: AgentSession,
    mint_agent_jwt: Callable[..., str],
    register_capability: Callable[..., CapabilityDefinition],
    grant_capability: Callable[..., CapabilityGrant],
) -> None:
    """JWT missing ``secure_action`` capability claim is denied over stdio."""
    server = _build_protected_secure_action_server(
        host_store,
        agent_store,
        capability_registry,
        register_capability,
    )
    grant_capability(agent_session.agent_id, "secure_action")
    token = mint_agent_jwt(capabilities=["echo"])

    response = await _stdio_tools_call(
        server,
        tool_call_params("secure_action", arguments={"action": "nope"}, jwt=token),
    )

    assert "result" in response
    assert response["result"]["isError"] is True
    assert CAPABILITY_DENIED in _call_tool_result_text(response)


@pytest.mark.serial_mcp
@pytest.mark.asyncio
async def test_subprocess_example_server_secure_action_denied_without_jwt() -> None:
    """Subprocess smoke: protected tool without JWT fails on example stdio server.

    Marked ``serial_mcp`` so CI keeps it out of pytest-xdist. The example
    server is a child process (``uv run python examples/mcp_auth_bridge/server.py``).
    """
    server_command = ["uv", "run", "python", str(_EXAMPLE_SERVER)]
    client = MCPClient(
        server_command,
        allowed_binaries=frozenset({"uv", "python", Path(sys.executable).name}),
    )

    async with client:
        secure = await client.call_tool("secure_action", {"action": "denied"})
        assert secure.is_error is True
        text = "".join(c.get("text", "") for c in secure.content if c.get("type") == "text")
        assert AUTH_REQUIRED in text
