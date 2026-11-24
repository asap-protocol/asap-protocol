"""MCP server implementation (spec 2025-11-25).

Runs over stdio: reads JSON-RPC messages (one per line) from stdin,
dispatches to handlers, writes responses to stdout.
"""

from __future__ import annotations

import asyncio
import io
import inspect
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import jsonschema

from asap.mcp.protocol import (
    INVALID_PARAMS,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    MCP_PROTOCOL_VERSION,
    PARSE_ERROR,
    CallToolRequestParams,
    CallToolResult,
    Implementation,
    InitializeResult,
    JSONRPCError,
    JSONRPCErrorResponse,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    ListToolsResult,
    TextContent,
    Tool,
)
from asap.observability import get_logger, is_debug_mode

logger = get_logger(__name__)

_INTERNAL_TOOL_ERROR_MESSAGE = "Internal tool error"

EMPTY_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": False}


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    """Typed per-tool registration entry stored on :class:`MCPServer._tools`.

    Replaces the former untyped positional 5-tuple
    ``(handler, schema, description, title, capability)`` so ``from_server`` and
    ``ProtectedMCPServer`` access fields by name (attribute access) rather than
    by fragile positional index. Capability metadata stays PER-TOOL-INSTANCE
    here, not hoisted onto a shared client/server, so two tools with different
    capability URNs registered on one server cannot inherit each other's grant
    (guards the S2 #240 URN-agnostic resolve-cache regression).

    Attributes:
        name: Unique tool name (e.g. ``echo``).
        handler: Callable invoked by ``tools/call`` (sync or async).
        schema: JSON Schema for the tool's parameters (``inputSchema``).
        metadata: Display metadata (``description`` + optional ``title``).
        capabilities: ASAP capability URN metadata for MCP Auth Bridge
            (MCP-MAP-002); ``None`` means the tool resolves to its own name.
    """

    name: str
    handler: Callable[..., Any]
    schema: dict[str, Any]
    metadata: ToolMetadata = field(default_factory=lambda: ToolMetadata())
    capabilities: str | None = None


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    """Human-readable display metadata for a registered tool (``tools/list``)."""

    description: str = ""
    title: str | None = None


class MCPServer:
    """MCP server with stdio transport and tools support.

    Register tools with register_tool(), then run with serve_stdio().
    Supports initialize, tools/list, and tools/call per MCP 2025-11-25.
    """

    def __init__(
        self,
        name: str = "asap-mcp-server",
        version: str = "1.0.0",
        title: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
    ) -> None:
        self._server_info = Implementation(
            name=name,
            version=version,
            title=title or name,
            description=description,
        )
        self._instructions = instructions
        self._tools: dict[str, ToolRegistration] = {}
        self._request_id_counter = 0

    def get_tool_capability(self, name: str) -> str | None:
        """Return optional ASAP capability metadata registered with the tool."""
        entry = self._tools.get(name)
        if entry is None:
            return None
        return entry.capabilities

    def register_tool(
        self,
        name: str,
        func: Callable[..., Any],
        schema: dict[str, Any],
        *,
        description: str = "",
        title: str | None = None,
        capability: str | None = None,
    ) -> None:
        """Register a tool that can be invoked via tools/call.

        Args:
            name: Unique tool name (e.g. "echo", "get_weather").
            func: Callable that receives keyword arguments from the client.
                  May be sync or async; return value is converted to text for
                  the result content. The framework passes raw arguments as
                  keyword dict; each tool must validate its own inputs.
            schema: JSON Schema for the tool's parameters (inputSchema).
                    Use EMPTY_INPUT_SCHEMA for no parameters.
            description: Human-readable description (required by spec).
            title: Optional display title.
            capability: Optional ASAP capability name for MCP Auth Bridge (MCP-MAP-002).
        """
        input_schema = schema if schema else EMPTY_INPUT_SCHEMA
        self._tools[name] = ToolRegistration(
            name=name,
            handler=func,
            schema=input_schema,
            metadata=ToolMetadata(
                description=description or f"Tool {name}",
                title=title,
            ),
            capabilities=capability,
        )

    def _get_capabilities(self) -> dict[str, Any]:
        """Server capabilities for initialize result."""
        caps: dict[str, Any] = {}
        if self._tools:
            caps["tools"] = {"listChanged": True}
        return caps

    def _handle_initialize(self, params: dict[str, Any] | None) -> dict[str, Any]:
        result = InitializeResult(
            protocolVersion=MCP_PROTOCOL_VERSION,
            capabilities=self._get_capabilities(),
            serverInfo=self._server_info,
            instructions=self._instructions,
        )
        return result.model_dump(by_alias=True, exclude_none=True)

    def _handle_tools_list(self, params: dict[str, Any] | None) -> dict[str, Any]:
        tools = [
            Tool(
                name=registration.name,
                description=registration.metadata.description,
                inputSchema=registration.schema,
                title=registration.metadata.title,
            )
            for registration in self._tools.values()
        ]
        result = ListToolsResult(tools=tools)
        return result.model_dump(by_alias=True, exclude_none=True)

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call; execute tool and return CallToolResult as dict.

        Raises:
            ValueError: With code INVALID_PARAMS for malformed params or tool
                argument mismatch (e.g. missing or invalid keyword arguments).
        """
        try:
            parsed = CallToolRequestParams(**params)
        except Exception as e:
            raise ValueError(f"Invalid params: {e}") from e

        if parsed.name not in self._tools:
            # Each content dict is dumped with exclude_none: the outer dump does not
            # reach into it, and MCP TS SDK 2.x rejects ``"annotations": null``.
            return CallToolResult(
                content=[
                    TextContent(text=f"Unknown tool: {parsed.name}").model_dump(
                        by_alias=True, exclude_none=True
                    )
                ],
                isError=True,
            ).model_dump(by_alias=True, exclude_none=True)

        registration = self._tools[parsed.name]
        func = registration.handler
        input_schema = registration.schema
        try:
            jsonschema.validate(instance=parsed.arguments, schema=input_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Invalid arguments: {e.message}") from e

        try:
            if inspect.iscoroutinefunction(func):
                out = await func(**parsed.arguments)
            else:
                loop = asyncio.get_running_loop()
                out = await loop.run_in_executor(None, lambda: func(**parsed.arguments))
        except TypeError as e:
            raise ValueError(f"Tool argument mismatch: {e}") from e
        except Exception as e:
            logger.exception("mcp.tool.error", tool=parsed.name, error=str(e))
            message = str(e) if is_debug_mode() else _INTERNAL_TOOL_ERROR_MESSAGE
            return CallToolResult(
                content=[TextContent(text=message).model_dump(by_alias=True, exclude_none=True)],
                isError=True,
            ).model_dump(by_alias=True, exclude_none=True)

        if isinstance(out, str):
            text = out
        elif isinstance(out, dict):
            text = json.dumps(out)
        else:
            text = str(out)
        return CallToolResult(
            content=[TextContent(text=text).model_dump(by_alias=True, exclude_none=True)],
            isError=False,
        ).model_dump(by_alias=True, exclude_none=True)

    async def _dispatch_request(self, req: JSONRPCRequest) -> str | None:
        """Dispatch a JSON-RPC request; return response line or None for notification."""
        method = req.method
        params = req.params or {}
        rid = req.id

        if method == "initialize":
            result = self._handle_initialize(params)
            return json.dumps(JSONRPCResponse(id=rid, result=result).model_dump(by_alias=True))

        if method == "tools/list":
            result = self._handle_tools_list(params)
            return json.dumps(JSONRPCResponse(id=rid, result=result).model_dump(by_alias=True))

        if method == "tools/call":
            try:
                result = await self._handle_tools_call(params)
            except ValueError as e:
                return json.dumps(
                    JSONRPCErrorResponse(
                        id=rid,
                        error=JSONRPCError(code=INVALID_PARAMS, message=str(e)),
                    ).model_dump(by_alias=True)
                )
            return json.dumps(JSONRPCResponse(id=rid, result=result).model_dump(by_alias=True))

        if method == "ping":
            return json.dumps(JSONRPCResponse(id=rid, result={}).model_dump(by_alias=True))

        return json.dumps(
            JSONRPCErrorResponse(
                id=rid,
                error=JSONRPCError(code=METHOD_NOT_FOUND, message=f"Method not found: {method}"),
            ).model_dump(by_alias=True)
        )

    def _dispatch_notification(self, notif: JSONRPCNotification) -> None:
        if notif.method == "notifications/initialized":
            logger.debug("mcp.initialized", message="Client sent initialized")
        elif notif.method == "notifications/cancelled":
            logger.debug("mcp.cancelled", params=notif.params)
        else:
            logger.debug("mcp.notification", method=notif.method, params=notif.params)

    async def serve_stdio(
        self,
        stdin: io.TextIOBase | None = None,
        stdout: io.TextIOBase | None = None,
    ) -> None:
        """Run the server over stdio (stdin/stdout). Blocks until stdin closes.

        Requests are processed sequentially: one request is handled at a time.
        Long-running tool calls will block other requests (e.g. ping, cancel)
        until they complete.

        Args:
            stdin: Optional input stream (default: sys.stdin).
            stdout: Optional output stream (default: sys.stdout).
        """
        _stdin = stdin if stdin is not None else sys.stdin
        _stdout = stdout if stdout is not None else sys.stdout
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)

        def read_stdin() -> dict[str, Any] | None:
            try:
                line = _stdin.readline()
            except (EOFError, OSError) as e:
                logger.debug("mcp.transport.closed", reason=str(e))
                return None
            if not line:
                return None
            line = line.rstrip("\n\r")
            if not line:
                return None
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": PARSE_ERROR, "message": "Parse error"},
                    }
                return cast("dict[str, Any]", data)
            except (json.JSONDecodeError, RecursionError):
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR, "message": "Parse error"},
                }

        async def producer() -> None:
            while True:
                raw = await loop.run_in_executor(None, read_stdin)
                await queue.put(raw)
                if raw is None:
                    break

        async def consumer() -> None:
            while True:
                raw = await queue.get()
                if raw is None:
                    break
                if (
                    isinstance(raw, dict)
                    and "method" not in raw
                    and raw.get("error", {}).get("code") == PARSE_ERROR
                ):
                    _stdout.write(json.dumps(raw) + "\n")
                    _stdout.flush()
                    continue
                if "id" in raw and raw.get("method"):
                    try:
                        req = JSONRPCRequest(**raw)
                        response_line = await self._dispatch_request(req)
                        if response_line:
                            _stdout.write(response_line + "\n")
                            _stdout.flush()
                    except Exception as e:
                        logger.exception("mcp.request_error", error=str(e))
                        rid = raw.get("id")
                        err = JSONRPCErrorResponse(
                            id=rid,
                            error=JSONRPCError(code=INTERNAL_ERROR, message=str(e)),
                        )
                        _stdout.write(json.dumps(err.model_dump(by_alias=True)) + "\n")
                        _stdout.flush()
                else:
                    try:
                        notif = JSONRPCNotification(**raw)
                        self._dispatch_notification(notif)
                    except Exception as e:
                        logger.debug("mcp.notification_error", error=str(e))

        await asyncio.gather(producer(), consumer())

    run_stdio = serve_stdio
