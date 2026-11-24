"""Read an HTTP refusal of an ASAP-protected endpoint, in any binding.

``@asap-protocol/mcp-auth`` and the A2A gateway refuse a token or a
grant in one HTTP form: a status, a ``WWW-Authenticate`` header with the
``Bearer`` scheme, and an OAuth error body whose ``error_description`` is the
``asap:*`` code. The ``mcp-auth-http`` and ``a2a-bridge`` profiles read it
through the :class:`HttpAnswer` protocol, so they share these readers. A
reason never quotes server text, except an ``asap:*`` code that it found, so a
token that a server echoes does not reach a check result.

Example::

    reason = INVALID_TOKEN_401.problem(response)
    assert reason is None, reason
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from asap.mcp.auth.errors import AUTH_REQUIRED, CAPABILITY_DENIED, INVALID_TOKEN

_ASAP_CODE = re.compile(r"asap:[a-z_]+")
_SAFE_WORD = re.compile(r"[a-z_]{1,40}")
_BEARER_SCHEME = re.compile(r"\s*bearer(\s|$)", re.IGNORECASE)
_ERROR_PARAMETER = re.compile(r'(?:^|[\s,])error=(?:"([^"]*)"|([^\s,]*))', re.IGNORECASE)


class HttpAnswer(Protocol):
    """One HTTP answer as a profile's transport recorded it."""

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> httpx.Headers: ...

    @property
    def json_body(self) -> dict[str, Any] | None: ...

    def describe(self) -> str:
        """Status, layer, and ``asap:*`` code of the answer, for a check message."""
        ...


def quoted_code(text: object) -> str:
    """The ``asap:*`` code in server text: safe to print, because it cannot hold a token."""
    match = _ASAP_CODE.search(text) if isinstance(text, str) else None
    return repr(match.group()) if match else "no asap:* code"


def quoted_word(value: object) -> str:
    """*value* when it is ``None`` or a short snake_case word, else ``another value``."""
    if value is None or (isinstance(value, str) and _SAFE_WORD.fullmatch(value)):
        return repr(value)
    return "another value"


def has_code(text: object, code: str) -> bool:
    """``<code>`` or ``<code>: <detail>``, the form of Python ``tool_error_result``."""
    return isinstance(text, str) and (text == code or text.startswith(f"{code}: "))


def has_bearer_challenge(response: HttpAnswer) -> bool:
    """True when the answer has a ``WWW-Authenticate`` header with the Bearer scheme."""
    return _BEARER_SCHEME.match(response.headers.get("www-authenticate", "")) is not None


@dataclass(frozen=True)
class BearerChallenge:
    """An HTTP refusal: status, Bearer challenge, and OAuth error body."""

    status: int
    oauth_error: str
    code: str
    # The ``error`` parameter of the challenge; None means it must be absent.
    challenge_error: str | None

    def problem(self, response: HttpAnswer) -> str | None:
        """None when the answer is this refusal, in the HTTP layer."""
        if response.status_code != self.status:
            return f"got {response.describe()}"
        if not has_bearer_challenge(response):
            return f"got HTTP {self.status} with no WWW-Authenticate header for the Bearer scheme"
        match = _ERROR_PARAMETER.search(response.headers["www-authenticate"])
        found_error = None if match is None else match.group(1) or match.group(2)
        if found_error != self.challenge_error:
            return f"got a Bearer challenge with error={quoted_word(found_error)}"
        body = response.json_body or {}
        description = body.get("error_description")
        if body.get("error") != self.oauth_error or not has_code(description, self.code):
            error = quoted_word(body.get("error"))
            return f"got a body with error={error} and {quoted_code(description)}"
        return None


# The three shared refusals. A missing token gets a challenge with no error
# parameter (RFC 6750 §3.1), but the body still says "invalid_token" (SDK OAuth body).
AUTH_REQUIRED_401 = BearerChallenge(401, "invalid_token", AUTH_REQUIRED, None)
INVALID_TOKEN_401 = BearerChallenge(401, "invalid_token", INVALID_TOKEN, "invalid_token")
CAPABILITY_DENIED_403 = BearerChallenge(
    403, "insufficient_scope", CAPABILITY_DENIED, "insufficient_scope"
)
