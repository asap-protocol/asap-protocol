"""Lock the OAuth access-token refresh window at its exact second.

``Token.is_expired`` treats ``now >= expires_at - buffer`` as expired.
``get_valid_token`` passes ``TOKEN_REFRESH_BUFFER_SECONDS``, so the equality
second refreshes a cached token and one second later reuses it.
"""

from __future__ import annotations

import httpx
import pytest

from asap.auth.oauth2 import TOKEN_REFRESH_BUFFER_SECONDS, OAuth2ClientCredentials, Token

_NOW = 1_700_000_000.0


def _token(expires_at: int) -> Token:
    return Token(access_token="access-token", expires_at=expires_at, token_type="Bearer")


def test_refresh_buffer_expires_on_the_equal_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token inside the refresh buffer is stale; one second outside stays cached."""
    monkeypatch.setattr("asap.auth.oauth2.time.time", lambda: _NOW)
    buffer = TOKEN_REFRESH_BUFFER_SECONDS
    at_window = _token(int(_NOW) + buffer)
    still_fresh = _token(int(_NOW) + buffer + 1)

    assert at_window.is_expired(buffer_seconds=buffer) is True
    assert still_fresh.is_expired(buffer_seconds=buffer) is False


def _credentials(handler: httpx.MockTransport) -> OAuth2ClientCredentials:
    return OAuth2ClientCredentials(
        client_id="client",
        client_secret="secret",
        token_url="https://auth.example.com/oauth/token",
        transport=handler,
    )


async def test_get_valid_token_refreshes_on_the_equal_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equality second hits the token endpoint; one extra second of life does not."""
    monkeypatch.setattr("asap.auth.oauth2.time.time", lambda: _NOW)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            status_code=200,
            json={
                "access_token": f"refreshed-{calls['count']}",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    buffer = TOKEN_REFRESH_BUFFER_SECONDS
    client = _credentials(httpx.MockTransport(handler))
    boundary = _token(int(_NOW) + buffer)
    client._cached_token = boundary
    refreshed = await client.get_valid_token()

    assert calls["count"] == 1
    assert refreshed.access_token == "refreshed-1"
    assert refreshed is not boundary

    fresh = _token(int(_NOW) + buffer + 1)
    client._cached_token = fresh
    reused = await client.get_valid_token()

    assert calls["count"] == 1
    assert reused is fresh


def test_zero_buffer_expires_on_the_exp_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no buffer, the token is expired at expires_at and fresh one second before."""
    monkeypatch.setattr("asap.auth.oauth2.time.time", lambda: _NOW)
    at_exp = _token(int(_NOW))
    one_second_left = _token(int(_NOW) + 1)

    assert at_exp.is_expired(buffer_seconds=0) is True
    assert one_second_left.is_expired(buffer_seconds=0) is False
