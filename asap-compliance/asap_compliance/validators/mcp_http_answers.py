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

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from asap.mcp.auth.errors import AUTH_REQUIRED, CAPABILITY_DENIED, INVALID_TOKEN

from asap_compliance.validators.mcp_http_transport import McpHttpResponse

_ASAP_CODE = re.compile(r"asap:[a-z_]+")
_SAFE_WORD = re.compile(r"[a-z_]{1,40}")
_BEARER_SCHEME = re.compile(r"\s*bearer(\s|$)", re.IGNORECASE)
_ERROR_PARAMETER = re.compile(r'(?:^|[\s,])error=(?:"([^"]*)"|([^\s,]*))', re.IGNORECASE)


def _quoted_code(text: object) -> str:
    """The ``asap:*`` code in server text: safe to print, because it cannot hold a token."""
    match = _ASAP_CODE.search(text) if isinstance(text, str) else None
    return repr(match.group()) if match else "no asap:* code"


def _quoted_word(value: object) -> str:
    if value is None or (isinstance(value, str) and _SAFE_WORD.fullmatch(value)):
        return repr(value)
    return "another value"


def _has_code(text: object, code: str) -> bool:
    """``<code>`` or ``<code>: <detail>``, the form of Python ``tool_error_result``."""
    return isinstance(text, str) and (text == code or text.startswith(f"{code}: "))


def _first_text(result: Mapping[str, Any]) -> object:
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return None
    return content[0].get("text") if content[0].get("type") == "text" else None


def describe_answer(response: McpHttpResponse) -> str:
    """Status, layer, and ``asap:*`` code of an answer, for a check message."""
    result = response.tool_result
    if result is None:
        description = (response.json_body or {}).get("error_description")
        return f"HTTP {response.status_code}, no tool result, {_quoted_code(description)}"
    layer = "tool error result" if result.get("isError") is True else "tool result"
    return f"HTTP {response.status_code}, {layer}, {_quoted_code(_first_text(result))}"


def has_bearer_challenge(response: McpHttpResponse) -> bool:
    """True when the answer has a ``WWW-Authenticate`` header with the Bearer scheme."""
    return _BEARER_SCHEME.match(response.headers.get("www-authenticate", "")) is not None


def tool_success_problem(response: McpHttpResponse) -> str | None:
    """None for HTTP 200 and a tool result to this request that is not an error."""
    result = response.tool_result
    if response.status_code != 200 or result is None or result.get("isError") is True:
        return f"got {describe_answer(response)}"
    return None


def tool_error_problem(response: McpHttpResponse, code: str) -> str | None:
    """None for HTTP 200 and a tool error result whose first text is ``code[: detail]``."""
    result = response.tool_result
    if response.status_code != 200 or result is None or result.get("isError") is not True:
        return f"got {describe_answer(response)}"
    if not _has_code(_first_text(result), code):
        return f"got {describe_answer(response)} as the first content item"
    return None


@dataclass(frozen=True)
class BearerChallenge:
    """An HTTP refusal: status, Bearer challenge, and OAuth error body."""

    status: int
    oauth_error: str
    code: str
    # The ``error`` parameter of the challenge; None means it must be absent.
    challenge_error: str | None

    def problem(self, response: McpHttpResponse) -> str | None:
        """None when the answer is this refusal, in the HTTP layer."""
        if response.status_code != self.status:
            return f"got {describe_answer(response)}"
        if not has_bearer_challenge(response):
            return f"got HTTP {self.status} with no WWW-Authenticate header for the Bearer scheme"
        match = _ERROR_PARAMETER.search(response.headers["www-authenticate"])
        found_error = None if match is None else match.group(1) or match.group(2)
        if found_error != self.challenge_error:
            return f"got a Bearer challenge with error={_quoted_word(found_error)}"
        body = response.json_body or {}
        description = body.get("error_description")
        if body.get("error") != self.oauth_error or not _has_code(description, self.code):
            error = _quoted_word(body.get("error"))
            return f"got a body with error={error} and {_quoted_code(description)}"
        return None


# The three HTTP refusals. A missing token gets a challenge with no error
# parameter (RFC 6750 §3.1), but the body still says "invalid_token" (SDK OAuth body).
AUTH_REQUIRED_401 = BearerChallenge(401, "invalid_token", AUTH_REQUIRED, None)
INVALID_TOKEN_401 = BearerChallenge(401, "invalid_token", INVALID_TOKEN, "invalid_token")
CAPABILITY_DENIED_403 = BearerChallenge(
    403, "insufficient_scope", CAPABILITY_DENIED, "insufficient_scope"
)
