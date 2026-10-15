"""Lock the OAuth access-token refresh window at its exact second.

``Token.is_expired`` treats ``now >= expires_at - buffer`` as expired.
``get_valid_token`` passes ``TOKEN_REFRESH_BUFFER_SECONDS``, so the equality
second is when a cached token must be refreshed.
"""

from __future__ import annotations

import pytest

from asap.auth.oauth2 import TOKEN_REFRESH_BUFFER_SECONDS, Token

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


def test_zero_buffer_expires_on_the_exp_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no buffer, the token is expired at expires_at and fresh one second before."""
    monkeypatch.setattr("asap.auth.oauth2.time.time", lambda: _NOW)
    at_exp = _token(int(_NOW))
    one_second_left = _token(int(_NOW) + 1)

    assert at_exp.is_expired(buffer_seconds=0) is True
    assert one_second_left.is_expired(buffer_seconds=0) is False
