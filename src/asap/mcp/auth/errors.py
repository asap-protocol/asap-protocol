"""MCP-facing ASAP auth error codes and result helpers (v2.5.0)."""

from __future__ import annotations

from typing import Any

from asap.mcp.protocol import CallToolResult, TextContent

AUTH_REQUIRED = "asap:auth_required"
INVALID_TOKEN = "asap:invalid_token"
CAPABILITY_DENIED = "asap:capability_denied"
CONSTRAINT_VIOLATION = "asap:constraint_violation"


def tool_error_result(code: str, detail: str | None = None) -> dict[str, Any]:
    """Build a ``tools/call`` error payload with ``isError: true``.

    Args:
        code: ASAP-namespaced error code (e.g. ``asap:auth_required``).
        detail: Optional human-readable detail appended after the code.

    Returns:
        Dict suitable for JSON-RPC ``result`` (``CallToolResult`` shape).

    Example:
        >>> tool_error_result(AUTH_REQUIRED)["isError"]
        True
    """
    text = code if detail is None else f"{code}: {detail}"
    # exclude_none here: the outer dump does not reach into plain content dicts,
    # and MCP TS SDK 2.x rejects ``"annotations": null``.
    content = TextContent(text=text).model_dump(by_alias=True, exclude_none=True)
    result = CallToolResult(content=[content])
    result.is_error = True
    return result.model_dump(by_alias=True, exclude_none=True)
