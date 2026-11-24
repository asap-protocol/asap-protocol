"""Read one A2A gateway answer for the ``a2a-bridge`` profile: which layer, which code.

The gateway answers in the layers of its README's refusal table: a token or
grant refusal at the HTTP layer (401, 403), before the JSON-RPC dispatcher; a
skill selection that names no listed skill as JSON-RPC ``-32602`` at HTTP 200;
a call that runs as a JSON-RPC result that holds one message. The token and
grant refusals both profiles share are in
:mod:`asap_compliance.validators.http_answers`. A reason never quotes server
text, except an ``asap:*`` code or an integer JSON-RPC error code.

Example::

    reason = message_success_problem(await transport.send_message("greet", args, bearer_token=t))
    assert reason is None, reason
"""

from __future__ import annotations

from asap.mcp.auth.errors import CONSTRAINT_VIOLATION

from asap_compliance.validators.a2a_transport import A2AHttpResponse
from asap_compliance.validators.http_answers import UnchallengedRefusal

INVALID_PARAMS = -32602
"""JSON-RPC ``InvalidParamsError``: the gateway's answer to a skill the card does not list."""

# A constraint violation: HTTP 403 with no challenge, because more scope
# does not help; the execute route answers the same.
CONSTRAINT_VIOLATION_403 = UnchallengedRefusal(403, "access_denied", CONSTRAINT_VIOLATION)


def message_success_problem(response: A2AHttpResponse) -> str | None:
    """None for HTTP 200 and a JSON-RPC result to this request whose message has a data part."""
    result = response.rpc_result or {}
    message = result.get("message")
    parts = message.get("parts") if isinstance(message, dict) else None
    has_data = isinstance(parts, list) and any(
        isinstance(part, dict) and isinstance(part.get("data"), dict) for part in parts
    )
    if response.status_code != 200 or not has_data:
        return f"got {response.describe()}"
    return None


def rpc_error_problem(response: A2AHttpResponse, code: int) -> str | None:
    """None for HTTP 200 and a JSON-RPC error with *code* for this request."""
    error = response.rpc_error or {}
    found = error.get("code")
    if response.status_code != 200 or type(found) is not int or found != code:
        return f"got {response.describe()}"
    return None
