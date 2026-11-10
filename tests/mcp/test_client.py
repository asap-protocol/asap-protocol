"""Unit tests for MCP client (error paths, timeout, not initialized)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asap.mcp.client import MCPClient


def test_allowed_binaries_rejects_unknown_binary() -> None:
    """allowed_binaries raises ValueError when binary not in allowlist."""
    with pytest.raises(ValueError, match="not in allowed_binaries"):
        MCPClient(
            ["/usr/bin/unknown-binary", "-m", "server"],
            allowed_binaries=frozenset({"python", "python3"}),
        )


def test_allowed_binaries_accepts_basename() -> None:
    """allowed_binaries validates basename of path (e.g. python3 from /usr/bin/python3)."""
    client = MCPClient(
        ["/usr/bin/python3", "-m", "asap.mcp.server_runner"],
        allowed_binaries=frozenset({"python", "python3"}),
    )
    assert client._server_command[0] == "/usr/bin/python3"


def test_allowed_binaries_none_disables_validation() -> None:
    """allowed_binaries=None (default) skips validation."""
    client = MCPClient(["any-binary", "arg"])
    assert client._server_command == ["any-binary", "arg"]


def test_allowed_binaries_uses_the_final_path_component() -> None:
    """Only the final path component is compared, and the compare is exact.

    ``curl`` and ``Python3`` miss a prefix check. ``python3-extra`` does not.
    """
    with pytest.raises(ValueError, match="binary 'curl' not in allowed_binaries"):
        MCPClient(
            ["python3/../curl", "-m", "server"],
            allowed_binaries=frozenset({"python3"}),
        )
    with pytest.raises(ValueError, match="binary 'Python3' not in allowed_binaries"):
        MCPClient(["Python3"], allowed_binaries=frozenset({"python3"}))
    with pytest.raises(ValueError, match="binary 'python3-extra' not in allowed_binaries"):
        MCPClient(["python3-extra"], allowed_binaries=frozenset({"python3"}))

    client = MCPClient(
        ["python3/../python3"],
        allowed_binaries=frozenset({"python3"}),
    )
    assert client._server_command[0] == "python3/../python3"


@pytest.mark.asyncio
async def test_client_send_without_connect_raises() -> None:
    """_send raises RuntimeError when not connected."""
    client = MCPClient([], receive_timeout=1.0)
    with pytest.raises(RuntimeError, match="Not connected"):
        await client._send({"jsonrpc": "2.0", "id": 1, "method": "ping"})


@pytest.mark.asyncio
async def test_client_receive_without_connect_raises() -> None:
    """_receive raises RuntimeError when not connected."""
    client = MCPClient([], receive_timeout=1.0)
    with pytest.raises(RuntimeError, match="Not connected"):
        await client._receive()


@pytest.mark.asyncio
async def test_client_connect_when_already_connected_raises() -> None:
    """connect() raises RuntimeError when already connected."""

    async def fake_connect() -> None:
        pass

    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = object()
    client._initialized = True
    with pytest.raises(RuntimeError, match="Already connected"):
        await client.connect()


@pytest.mark.asyncio
async def test_client_list_tools_without_connect_raises() -> None:
    """list_tools raises RuntimeError when not initialized."""
    client = MCPClient(["true"], receive_timeout=1.0)
    with pytest.raises(RuntimeError, match="Not initialized"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_client_call_tool_without_connect_raises() -> None:
    """call_tool raises RuntimeError when not initialized."""
    client = MCPClient(["true"], receive_timeout=1.0)
    with pytest.raises(RuntimeError, match="Not initialized"):
        await client.call_tool("echo", {})


@pytest.mark.asyncio
async def test_client_next_id_increments() -> None:
    """_next_id returns incrementing request ids (default str)."""
    client = MCPClient(["true"])
    assert client._next_id() == "1"
    assert client._next_id() == "2"


@pytest.mark.asyncio
async def test_client_next_id_int_type() -> None:
    """_next_id returns int ids when request_id_type is 'int'."""
    client = MCPClient(["true"], request_id_type="int")
    assert client._next_id() == 1
    assert client._next_id() == 2


@pytest.mark.asyncio
async def test_client_receive_timeout_raises() -> None:
    """_receive raises RuntimeError when server does not respond within timeout."""
    client = MCPClient(["true"], receive_timeout=0.1)
    never_complete: asyncio.Future[bytes] = asyncio.Future()
    client._process = MagicMock()
    client._process.stdout = MagicMock()
    client._process.stdout.readline = MagicMock(return_value=never_complete)
    with pytest.raises(RuntimeError, match="No response within"):
        await client._receive()


@pytest.mark.asyncio
async def test_client_connect_no_response_raises() -> None:
    """connect() raises when server sends no response to initialize."""

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        return proc

    with patch(
        "asap.mcp.client.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec
    ):
        client = MCPClient(["/bin/true"], receive_timeout=1.0)
        client._receive = AsyncMock(return_value=None)
        with pytest.raises(RuntimeError, match="No response to initialize"):
            await client.connect()


@pytest.mark.asyncio
async def test_client_connect_error_response_raises() -> None:
    """connect() raises when server returns error in initialize."""

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        return proc

    with patch(
        "asap.mcp.client.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec
    ):
        client = MCPClient(["/bin/true"], receive_timeout=1.0)
        client._receive = AsyncMock(return_value={"error": {"message": "bad init"}})
        with pytest.raises(RuntimeError, match="Initialize failed"):
            await client.connect()


def _make_connected_client() -> MCPClient:
    """Return a client with _process and _initialized set (for testing list_tools/call_tool)."""
    client = MCPClient(["/bin/true"], receive_timeout=1.0)
    client._process = MagicMock()
    client._process.stdin = MagicMock()
    client._process.stdin.write = MagicMock()
    client._process.stdin.drain = AsyncMock()
    client._process.stdout = MagicMock()
    client._initialized = True
    return client


@pytest.mark.asyncio
async def test_client_list_tools_no_response_raises() -> None:
    """list_tools raises when server sends no response."""
    client = _make_connected_client()
    client._receive = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="No response to tools/list"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_client_list_tools_error_response_raises() -> None:
    """list_tools raises when server returns error."""
    client = _make_connected_client()
    client._receive = AsyncMock(return_value={"error": {"message": "list failed"}})
    with pytest.raises(RuntimeError, match="tools/list failed"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_client_call_tool_no_response_raises() -> None:
    """call_tool raises when server sends no response."""
    client = _make_connected_client()
    client._receive = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="No response to tools/call"):
        await client.call_tool("echo", {"message": "hi"})


@pytest.mark.asyncio
async def test_client_call_tool_error_response_returns_is_error() -> None:
    """call_tool returns CallToolResult with is_error when server returns error."""
    client = _make_connected_client()
    client._receive = AsyncMock(return_value={"error": {"code": -32603, "message": "tool failed"}})
    result = await client.call_tool("echo", {"message": "hi"})
    assert result.is_error is True
    assert any("tool failed" in (c.get("text") or "") for c in result.content)


@pytest.mark.asyncio
async def test_client_receive_without_timeout() -> None:
    """_receive without timeout reads line directly."""
    client = MCPClient(["true"], receive_timeout=None)  # No timeout
    client._process = MagicMock()
    client._process.stdout = MagicMock()

    async def readline() -> bytes:
        return b'{"jsonrpc":"2.0","result":{}}\n'

    client._process.stdout.readline = readline
    result = await client._receive()
    assert result == {"jsonrpc": "2.0", "result": {}}


@pytest.mark.asyncio
async def test_client_receive_empty_line_returns_none() -> None:
    """_receive returns None on empty line (b'')."""
    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = MagicMock()
    client._process.stdout = MagicMock()

    async def readline() -> bytes:
        return b""

    client._process.stdout.readline = readline
    result = await client._receive()
    assert result is None


@pytest.mark.asyncio
async def test_client_receive_whitespace_line_returns_none() -> None:
    """_receive returns None on whitespace-only line."""
    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = MagicMock()
    client._process.stdout = MagicMock()

    async def readline() -> bytes:
        return b"   \n"

    client._process.stdout.readline = readline
    result = await client._receive()
    assert result is None


@pytest.mark.asyncio
async def test_client_receive_json_decode_error_returns_none() -> None:
    """_receive returns None on invalid JSON (logs warning)."""
    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = MagicMock()
    client._process.stdout = MagicMock()

    async def readline() -> bytes:
        return b"not valid json\n"

    client._process.stdout.readline = readline
    result = await client._receive()
    assert result is None


@pytest.mark.asyncio
async def test_client_receive_json_array_returns_none() -> None:
    """_receive returns None when server sends JSON array (non-dict)."""
    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = MagicMock()
    client._process.stdout = MagicMock()

    async def readline() -> bytes:
        return b"[1, 2, 3]\n"

    client._process.stdout.readline = readline
    result = await client._receive()
    assert result is None


@pytest.mark.asyncio
async def test_client_list_tools_success() -> None:
    """list_tools returns list of Tool on success."""
    client = _make_connected_client()
    client._receive = AsyncMock(
        return_value={
            "result": {
                "tools": [
                    {"name": "echo", "description": "Echo input", "inputSchema": {"type": "object"}}
                ]
            }
        }
    )
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"


@pytest.mark.asyncio
async def test_client_call_tool_success() -> None:
    """call_tool returns CallToolResult on success."""
    client = _make_connected_client()
    client._receive = AsyncMock(
        return_value={
            "result": {
                "content": [{"type": "text", "text": "hello"}],
                "isError": False,
            }
        }
    )
    result = await client.call_tool("echo", {"message": "hi"})
    assert result.is_error is False
    assert len(result.content) == 1


@pytest.mark.asyncio
async def test_client_call_tool_with_meta() -> None:
    """call_tool forwards optional _meta (e.g. asap_agent_jwt) in tools/call params."""
    client = _make_connected_client()
    client._send = AsyncMock()
    client._receive = AsyncMock(
        return_value={
            "result": {
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
            }
        }
    )
    await client.call_tool(
        "secure_action",
        {"action": "ping"},
        meta={"asap_agent_jwt": "test-jwt"},
    )
    sent = client._send.await_args_list[-1][0][0]
    assert sent["params"]["_meta"]["asap_agent_jwt"] == "test-jwt"


@pytest.mark.asyncio
async def test_client_disconnect() -> None:
    """disconnect closes stdin, terminates, and waits."""
    client = MCPClient(["true"], receive_timeout=1.0)
    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_stdin.close = MagicMock()
    mock_stdin.wait_closed = AsyncMock()
    mock_process.stdin = mock_stdin
    mock_process.terminate = MagicMock()
    mock_process.wait = AsyncMock()
    client._process = mock_process
    client._initialized = True

    await client.disconnect()

    mock_stdin.close.assert_called_once()
    mock_process.terminate.assert_called_once()
    assert client._initialized is False
    assert client._process is None


@pytest.mark.asyncio
async def test_client_disconnect_no_process() -> None:
    """disconnect does nothing when not connected."""
    client = MCPClient(["true"], receive_timeout=1.0)
    client._process = None
    client._initialized = True

    await client.disconnect()

    assert client._initialized is False


@pytest.mark.asyncio
async def test_client_context_manager() -> None:
    """__aenter__ calls connect, __aexit__ calls disconnect."""
    client = MCPClient(["/bin/true"], receive_timeout=1.0)
    client.connect = AsyncMock(return_value=MagicMock())
    client.disconnect = AsyncMock()

    async with client as c:
        assert c is client
        client.connect.assert_called_once()

    client.disconnect.assert_called_once()
