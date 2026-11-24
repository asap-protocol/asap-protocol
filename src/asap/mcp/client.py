"""MCP client implementation (spec 2025-11-25).

Connects to an MCP server over stdio (subprocess) or provides a transport
abstraction for sending requests and receiving responses.

Security: ``server_command`` must come from a trusted source (e.g. config file).
Do not derive from user input; untrusted input could allow command injection.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal, cast

from asap.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    CallToolRequestParams,
    CallToolResult,
    Implementation,
    InitializeRequestParams,
    InitializeResult,
    ListToolsResult,
    Tool,
)
from asap.observability import get_logger

logger = get_logger(__name__)


class MCPClient:
    """MCP client with stdio transport.

    Connects to an MCP server process via stdin/stdout, performs
    initialize handshake, then supports tools/list and tools/call.
    """

    def __init__(
        self,
        server_command: list[str],
        *,
        name: str = "asap-mcp-client",
        version: str = "1.0.0",
        receive_timeout: float | None = 60.0,
        request_id_type: Literal["int", "str"] = "str",
        allowed_binaries: frozenset[str] | None = None,
        subprocess_env: dict[str, str] | None = None,
    ) -> None:
        if allowed_binaries is not None and server_command:
            binary = os.path.basename(server_command[0])
            if binary not in allowed_binaries:
                raise ValueError(
                    f"server_command binary '{binary}' not in allowed_binaries {sorted(allowed_binaries)}. "
                    "Use allowed_binaries=None to disable validation."
                )
        self._server_command = server_command
        self._client_info = Implementation(name=name, version=version)
        self._receive_timeout = receive_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._initialized = False
        self._request_id = 0
        self._use_str_ids = request_id_type == "str"
        self._init_result: InitializeResult | None = None
        self._subprocess_env = subprocess_env

    def _next_id(self) -> str | int:
        self._request_id += 1
        return str(self._request_id) if self._use_str_ids else self._request_id

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        """Server subprocess stderr stream (``None`` when disconnected)."""
        if self._process is None:
            return None
        return self._process.stderr

    async def _send(self, payload: dict[str, Any]) -> None:
        """Send one JSON-RPC message (request or notification) to server stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Not connected; call connect() first")
        line = json.dumps(payload) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _receive(self) -> dict[str, Any] | None:
        """Read one JSON-RPC response from server stdout."""
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Not connected; call connect() first")
        read_coro = self._process.stdout.readline()
        if self._receive_timeout is not None:
            try:
                raw_line = await asyncio.wait_for(read_coro, timeout=self._receive_timeout)
            except asyncio.TimeoutError:
                raise RuntimeError(f"No response within {self._receive_timeout}s") from None
        else:
            raw_line = await read_coro
        if not raw_line:
            return None
        line = raw_line.decode("utf-8").rstrip("\n\r")
        if not line:
            return None
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                return None
            return cast("dict[str, Any]", data)
        except json.JSONDecodeError as e:
            logger.warning("mcp.client.parse_error", error=str(e))
            return None

    async def connect(self) -> InitializeResult:
        """Start the server process and perform initialize handshake.

        Returns:
            InitializeResult from the server.

        Raises:
            RuntimeError: If already connected or server fails to respond.
        """
        if self._process is not None:
            raise RuntimeError("Already connected")
        self._process = await asyncio.create_subprocess_exec(
            *self._server_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._subprocess_env,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("Failed to get server stdin/stdout")

        req_id = self._next_id()
        init_req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": InitializeRequestParams(
                protocolVersion=MCP_PROTOCOL_VERSION,
                capabilities={},
                clientInfo=self._client_info,
            ).model_dump(by_alias=True, exclude_none=True),
        }
        await self._send(init_req)
        raw = await self._receive()
        if raw is None:
            raise RuntimeError("No response to initialize")
        if "error" in raw:
            err = raw["error"]
            raise RuntimeError(f"Initialize failed: {err.get('message', err)}")
        self._init_result = InitializeResult.model_validate(raw["result"])
        self._initialized = True

        await self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        return self._init_result

    async def list_tools(self) -> list[Tool]:
        """Request the list of tools from the server.

        Returns:
            List of Tool definitions.
        """
        if not self._initialized:
            raise RuntimeError("Not initialized; call connect() first")
        req_id = self._next_id()
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/list",
                "params": {},
            }
        )
        raw = await self._receive()
        if raw is None:
            raise RuntimeError("No response to tools/list")
        if "error" in raw:
            err = raw["error"]
            raise RuntimeError(f"tools/list failed: {err.get('message', err)}")
        result = ListToolsResult(**raw["result"])
        return result.tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Invoke a tool by name with the given arguments.

        Args:
            name: Tool name (as returned by list_tools).
            arguments: Tool arguments (keyword dict).
            meta: Optional MCP ``_meta`` dict (e.g. ``{"asap_agent_jwt": "<token>"}``).

        Returns:
            CallToolResult with content and is_error.
        """
        if not self._initialized:
            raise RuntimeError("Not initialized; call connect() first")
        req_id = self._next_id()
        params = CallToolRequestParams(name=name, arguments=arguments or {}, _meta=meta)
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                # exclude_none drops only an unset ``_meta`` (it does not reach into the
                # argument dicts); MCP TS SDK 2.x rejects ``"_meta": null``.
                "params": params.model_dump(by_alias=True, exclude_none=True),
            }
        )
        raw = await self._receive()
        if raw is None:
            raise RuntimeError("No response to tools/call")
        if "error" in raw:
            err = raw["error"]
            return CallToolResult(
                content=[{"type": "text", "text": err.get("message", str(err))}],
                isError=True,
            )
        return CallToolResult.model_validate(raw["result"])

    async def disconnect(self) -> None:
        """Close the connection to the server (and terminate the process)."""
        if self._process is not None:
            if self._process.stdin:
                self._process.stdin.close()
                await self._process.stdin.wait_closed()
            self._process.terminate()
            await self._process.wait()
            self._process = None
        self._initialized = False

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
