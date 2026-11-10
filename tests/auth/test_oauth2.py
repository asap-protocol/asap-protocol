"""Unit tests for OAuth2 client (client_credentials flow).

Covers token acquisition, caching, auto-refresh, error handling,
and token parsing (expires_in, expires_at, default lifetime).
"""

import importlib
import time
from types import ModuleType

import httpx
import pytest
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client

from asap.auth.oauth2 import (
    DEFAULT_TOKEN_LIFETIME_SECONDS,
    OAuth2ClientCredentials,
    TOKEN_REFRESH_BUFFER_SECONDS,
    Token,
    authlib_httpx_module,
)

_http = authlib_httpx_module()
try:
    _httpx2: ModuleType | None = importlib.import_module("httpx2")
except ImportError:
    _httpx2 = None


async def test_get_access_token_with_mocked_endpoint() -> None:
    """Verify OAuth2ClientCredentials can obtain token from mocked token endpoint."""

    def mock_transport(request: _http.Request) -> _http.Response:
        if request.url.path.endswith("/token") or "token" in str(request.url):
            return _http.Response(
                status_code=200,
                json={
                    "access_token": "mock-access-token-123",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test-client",
        client_secret="test-secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    token = await client.get_access_token()

    assert isinstance(token, Token)
    assert token.access_token == "mock-access-token-123"
    assert token.token_type == "Bearer"
    assert token.expires_at > 0


async def test_get_valid_token_reuses_cached_token_when_valid() -> None:
    """Verify get_valid_token returns cached token when not expired."""
    call_count = 0

    def mock_transport(request: _http.Request) -> _http.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path.endswith("/token") or "token" in str(request.url):
            return _http.Response(
                status_code=200,
                json={
                    "access_token": "cached-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test-client",
        client_secret="test-secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    first = await client.get_valid_token()
    second = await client.get_valid_token()

    assert first is second
    assert first.access_token == "cached-token"
    assert call_count == 1


async def test_get_valid_token_refreshes_when_near_expiry() -> None:
    """Verify get_valid_token refreshes when token is within refresh buffer of expiry."""

    def mock_transport(request: _http.Request) -> _http.Response:
        if request.url.path.endswith("/token") or "token" in str(request.url):
            return _http.Response(
                status_code=200,
                json={
                    "access_token": "refreshed-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test-client",
        client_secret="test-secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )
    # Set cache to a token that is within buffer of expiry (expires in 15s < 30s buffer)
    now = int(time.time())
    client._cached_token = Token(
        access_token="stale-token",
        expires_at=now + 15,
        token_type="Bearer",
    )

    token = await client.get_valid_token()

    assert token.access_token == "refreshed-token"
    assert token.expires_at >= now + 3600 - 10  # Allow small clock skew
    assert client._cached_token.access_token == "refreshed-token"


async def test_token_is_expired_with_buffer() -> None:
    """Sanity check: token within TOKEN_REFRESH_BUFFER_SECONDS is considered expired."""
    now = int(time.time())
    token = Token(
        access_token="x",
        expires_at=now + TOKEN_REFRESH_BUFFER_SECONDS - 1,
        token_type="Bearer",
    )
    assert token.is_expired(buffer_seconds=TOKEN_REFRESH_BUFFER_SECONDS) is True
    # Use a margin well above buffer so the check does not flake (clock can advance 1s).
    token_fresh = Token(
        access_token="x",
        expires_at=now + TOKEN_REFRESH_BUFFER_SECONDS + 60,
        token_type="Bearer",
    )
    assert token_fresh.is_expired(buffer_seconds=TOKEN_REFRESH_BUFFER_SECONDS) is False


async def test_get_access_token_raises_on_invalid_credentials() -> None:
    """Verify get_access_token raises when token endpoint returns 401 (Authlib raises OAuthError)."""

    def mock_transport(request: _http.Request) -> _http.Response:
        if "token" in str(request.url):
            return _http.Response(
                status_code=401,
                json={"error": "invalid_client", "error_description": "Bad credentials"},
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="bad",
        client_secret="bad",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    with pytest.raises(OAuthError) as exc_info:
        await client.get_access_token()

    assert exc_info.value.error == "invalid_client"


async def test_get_access_token_raises_on_network_error() -> None:
    """Verify get_access_token raises on network/connection errors."""

    def mock_transport(request: _http.Request) -> _http.Response:
        raise _http.ConnectError("Connection refused")

    client = OAuth2ClientCredentials(
        client_id="test",
        client_secret="secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    with pytest.raises(_http.ConnectError) as exc_info:
        await client.get_access_token()

    assert "Connection refused" in str(exc_info.value)


async def test_get_valid_token_raises_when_refresh_fails() -> None:
    """Verify get_valid_token raises when cached token is expired and refresh returns 401."""

    def mock_transport(request: _http.Request) -> _http.Response:
        if "token" in str(request.url):
            return _http.Response(
                status_code=401,
                json={"error": "invalid_client"},
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test",
        client_secret="secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )
    now = int(time.time())
    client._cached_token = Token(
        access_token="stale",
        expires_at=now - 60,
        token_type="Bearer",
    )

    with pytest.raises(OAuthError) as exc_info:
        await client.get_valid_token()

    assert exc_info.value.error == "invalid_client"


async def test_token_parsing_uses_expires_at_when_present() -> None:
    """Verify token with expires_at in response is parsed correctly."""
    fixed_expires_at = 9999999999

    def mock_transport(request: _http.Request) -> _http.Response:
        if "token" in str(request.url):
            return _http.Response(
                status_code=200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "expires_at": fixed_expires_at,
                },
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test",
        client_secret="secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    token = await client.get_access_token()

    assert token.expires_at == fixed_expires_at
    assert token.access_token == "at"


async def test_token_parsing_uses_default_lifetime_when_no_expiry() -> None:
    """Verify token without expires_in or expires_at uses DEFAULT_TOKEN_LIFETIME_SECONDS."""

    def mock_transport(request: _http.Request) -> _http.Response:
        if "token" in str(request.url):
            return _http.Response(
                status_code=200,
                json={
                    "access_token": "no-expiry-token",
                    "token_type": "Bearer",
                },
            )
        return _http.Response(status_code=404)

    client = OAuth2ClientCredentials(
        client_id="test",
        client_secret="secret",
        token_url="https://auth.example.com/oauth/token",
        transport=_http.MockTransport(mock_transport),
    )

    before = int(time.time())
    token = await client.get_access_token()
    after = int(time.time())

    assert token.access_token == "no-expiry-token"
    assert (
        before + DEFAULT_TOKEN_LIFETIME_SECONDS
        <= token.expires_at
        <= after + DEFAULT_TOKEN_LIFETIME_SECONDS + 2
    )


def test_authlib_httpx_module_is_oauth_client_base() -> None:
    """authlib_httpx_module is the package AsyncOAuth2Client subclasses."""
    http = authlib_httpx_module()

    assert http.__name__ in {"httpx", "httpx2"}
    assert issubclass(AsyncOAuth2Client, http.AsyncClient)


def test_rejects_transport_from_the_other_http_stack() -> None:
    """A transport from the unbound HTTP stack fails before the token request."""
    bound_name = _http.__name__
    if bound_name == "httpx":
        if _httpx2 is None:
            pytest.skip("httpx2 is not installed")
        foreign_http: ModuleType = _httpx2
        expected_stack = "httpx"
    else:
        foreign_http = httpx
        expected_stack = bound_name

    foreign = foreign_http.MockTransport(lambda _request: foreign_http.Response(status_code=204))
    with pytest.raises(TypeError, match=rf"not a {expected_stack}\.AsyncBaseTransport"):
        OAuth2ClientCredentials(
            client_id="test",
            client_secret="secret",
            token_url="https://auth.example.com/oauth/token",
            transport=foreign,
        )
