"""Read an HTTP answer of an ASAP-protected endpoint, in any binding.

``@asap-protocol/mcp-auth`` and the A2A gateway put an ``asap:*``
code in the answers they refuse. The ``mcp-auth-http`` and ``a2a-bridge``
profiles share these readers. A reason never quotes server text, except an
``asap:*`` code that it found, so a token that a server echoes does not reach a
check result.

Example::

    assert quoted_code("asap:invalid_token: replay") == "'asap:invalid_token'"
"""

from __future__ import annotations

import re

_ASAP_CODE = re.compile(r"asap:[a-z_]+")
_SAFE_WORD = re.compile(r"[a-z_]{1,40}")


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
