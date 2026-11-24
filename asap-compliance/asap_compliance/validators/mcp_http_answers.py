"""Read one HTTP MCP answer for the ``mcp-auth-http`` profile: which layer, which code.

``@asap-protocol/mcp-auth`` reports token errors as HTTP 401 with a Bearer challenge,
and grant errors as a tool result, or as HTTP 403 on the external-token path
(PRD MCP-TS-002). A check passes only for the right code in the
right layer. Each ``*_problem`` function returns ``None`` for the expected answer, or
a short reason. A reason never quotes server text, except an ``asap:*`` code that it
found, so a token that a server echoes does not reach a check result.

Example::

    reason = INVALID_TOKEN_401.problem(response)
    assert reason is None, reason
"""

from __future__ import annotations

from typing import Any

from asap_compliance.validators.http_answers import (
    AUTH_REQUIRED_401,
    CAPABILITY_DENIED_403,
    INVALID_TOKEN_401,
    BearerChallenge,
    has_bearer_challenge,
    has_code,
)
from asap_compliance.validators.mcp_http_transport import McpHttpResponse, first_text

# The refusal readers live in http_answers.py, which the a2a-bridge profile shares;
# they stay importable from here for the mcp-auth-http profile.
__all__ = [
    "AUTH_REQUIRED_401",
    "CAPABILITY_DENIED_403",
    "INVALID_TOKEN_401",
    "BearerChallenge",
    "describe_answer",
    "has_bearer_challenge",
    "tool_error_problem",
    "tool_success_problem",
]


def describe_answer(response: McpHttpResponse) -> str:
    """Status, layer, and ``asap:*`` code of an answer, for a check message."""
    return response.describe()


def _has_content_fields(block: object) -> bool:
    """Check the required fields of each content block in MCP revision 2026-07-28."""
    match block:
        case {"type": "text", "text": str()}:
            return True
        case {"type": "image" | "audio", "data": str(), "mimeType": str()}:
            return True
        case {"type": "resource_link", "name": str(), "uri": str()}:
            return True
        case {
            "type": "resource",
            "resource": {"uri": str(), "text": str()} | {"uri": str(), "blob": str()},
        }:
            return True
        case _:
            return False


def _completed_result(response: McpHttpResponse) -> dict[str, Any] | None:
    result = response.tool_result
    if response.status_code != 200 or result is None:
        return None
    content = result.get("content")
    if result.get("resultType") != "complete" or not isinstance(content, list):
        return None
    if not all(_has_content_fields(block) for block in content):
        return None
    return result


def tool_success_problem(response: McpHttpResponse) -> str | None:
    """None for HTTP 200 and a tool result to this request that is not an error."""
    result = _completed_result(response)
    if result is None or result.get("isError", False) is not False:
        return f"got {describe_answer(response)}"
    return None


def tool_error_problem(response: McpHttpResponse, code: str) -> str | None:
    """None for HTTP 200 and a tool error result whose first text is ``code[: detail]``."""
    result = _completed_result(response)
    if result is None or result.get("isError") is not True:
        return f"got {describe_answer(response)}"
    if not has_code(first_text(result), code):
        return f"got {describe_answer(response)} as the first content item"
    return None
