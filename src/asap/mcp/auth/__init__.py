"""MCP Auth Bridge: ASAP auth on native ``MCPServer`` ``tools/call``.

MCP auth belongs inside the MCP package (``asap.mcp.auth``), next to
:mod:`asap.mcp.protocol` and :mod:`asap.mcp.server`, rather than under the
OpenAPI-centric ``asap.adapters`` namespace. Import from here: v2.6.0
removes the old path under ``asap.adapters`` (see ``docs/migration.md``).

Public surface: ``protect_server``, ``MCPAuthConfig``,
``ProtectedMCPServer``, ``resolve_jwt_extractor``.

Example:
    >>> from asap.mcp.auth import MCPAuthConfig, protect_server
    >>> protected = protect_server(server, config)
"""

from __future__ import annotations

from asap.mcp.auth.auth_middleware import protect_server
from asap.mcp.auth.config import MCPAuthConfig, resolve_jwt_extractor
from asap.mcp.auth.protected_server import ProtectedMCPServer

__all__ = [
    "MCPAuthConfig",
    "ProtectedMCPServer",
    "protect_server",
    "resolve_jwt_extractor",
]
