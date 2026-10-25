"""Per-call OAuth scope overrides the constructor scope on a cold token fetch.

``get_access_token(scope=...)`` and a cold ``get_valid_token(scope=...)`` send
that scope to the token endpoint. Omitting the argument sends the constructor
scope. A warm cache that ignores a later scope change is not asserted here.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from asap.auth.oauth2 import OAuth2ClientCredentials

_TOKEN_URL = "https://auth.example.com/oauth/token"
_CONSTRUCTOR_SCOPE = "asap:read"
_CALL_SCOPE = "asap:execute asap:admin"


def _scope_of(request: httpx.Request) -> str:
    """Return the single ``scope`` form field from a token request."""
    parsed = parse_qs(request.content.decode())
    values = parsed.get("scope")
    if values is None or len(values) != 1:
        raise AssertionError(f"expected one scope field, body={request.content!r}")
    return values[0]


def _transport(seen: list[str]) -> httpx.MockTransport:
    """Token endpoint that records the scope it was asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_scope_of(request))
        return httpx.Response(
            status_code=200,
            json={
                "access_token": "scoped-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport, scope: str | None) -> OAuth2ClientCredentials:
    """Client credentials client bound to ``transport``."""
    return OAuth2ClientCredentials(
        client_id="test-client",
        client_secret="test-secret",
        token_url=_TOKEN_URL,
        scope=scope,
        transport=transport,
    )


async def test_get_access_token_sends_constructor_scope_when_omitted() -> None:
    """No call-time scope means the constructor scope is requested."""
    seen: list[str] = []
    client = _client(_transport(seen), _CONSTRUCTOR_SCOPE)

    token = await client.get_access_token()

    assert token.access_token == "scoped-token"
    assert seen == [_CONSTRUCTOR_SCOPE]


async def test_get_access_token_sends_call_scope_instead_of_constructor() -> None:
    """An explicit scope replaces the constructor scope on the token request."""
    seen: list[str] = []
    client = _client(_transport(seen), _CONSTRUCTOR_SCOPE)

    await client.get_access_token(scope=_CALL_SCOPE)

    assert seen == [_CALL_SCOPE]


async def test_cold_get_valid_token_sends_call_scope() -> None:
    """A cache miss forwards the call-time scope into ``get_access_token``."""
    seen: list[str] = []
    client = _client(_transport(seen), _CONSTRUCTOR_SCOPE)

    token = await client.get_valid_token(scope=_CALL_SCOPE)

    assert token.access_token == "scoped-token"
    assert seen == [_CALL_SCOPE]
