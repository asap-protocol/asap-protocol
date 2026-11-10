"""OAuth2 client for ASAP Protocol.

Provides token acquisition and refresh using:
- client_credentials: Machine-to-machine authentication (agent-to-agent)
- authorization_code: Human-in-the-loop scenarios (planned)

Uses Authlib's AsyncOAuth2Client internally (ADR-12).
"""

import asyncio
import importlib
import time
from types import ModuleType
from typing import Any, Optional, cast

from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import Field

from asap.models.base import ASAPBaseModel
from asap.observability import get_logger

logger = get_logger(__name__)

TOKEN_REFRESH_BUFFER_SECONDS = 30
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
_TOKEN_ENDPOINT_TIMEOUT_SECONDS = 10.0
_OAUTH_HTTP_PACKAGES = frozenset({"httpx", "httpx2"})


class Token(ASAPBaseModel):
    """OAuth2 access token with expiry metadata.

    Represents a token obtained from an OAuth2 token endpoint.

    Attributes:
        access_token: The opaque access token string.
        expires_at: Unix timestamp when the token expires.
        token_type: Token type, typically "Bearer".
    """

    access_token: str = Field(..., description="The opaque access token string")
    expires_at: int = Field(..., description="Unix timestamp when the token expires")
    token_type: str = Field(default="Bearer", description="Token type for Authorization header")

    def is_expired(self, buffer_seconds: float = 0) -> bool:
        """Check if the token is expired or within buffer of expiry.

        Args:
            buffer_seconds: Seconds before actual expiry to consider token expired.

        Returns:
            True if token is expired or within buffer, False otherwise.
        """
        return time.time() >= (self.expires_at - buffer_seconds)


def _parse_token_response(raw_token: dict[str, Any]) -> Token:
    """Convert Authlib's raw token dict into an ASAP Token model.

    Args:
        raw_token: Dict returned by Authlib's fetch_token().

    Returns:
        Token with access_token, expires_at, and token_type.
    """
    access_token: str = raw_token["access_token"]
    token_type: str = raw_token.get("token_type", "Bearer")

    if "expires_at" in raw_token:
        expires_at = int(raw_token["expires_at"])
    elif "expires_in" in raw_token:
        expires_at = int(time.time()) + int(raw_token["expires_in"])
    else:
        expires_at = int(time.time()) + DEFAULT_TOKEN_LIFETIME_SECONDS

    return Token(
        access_token=access_token,
        expires_at=expires_at,
        token_type=token_type,
    )


def _resolve_authlib_httpx_module() -> ModuleType:
    """Return the httpx package that bases Authlib's async OAuth client.

    Authlib 1.9 subclasses ``httpx2.AsyncClient`` when that package imports,
    and ``httpx.AsyncClient`` only as a fallback
    (``authlib.integrations.httpx_client._compat``). The class ``__module__``
    is the real package, including when Authlib aliases ``httpx`` as ``httpx2``.
    """
    for base in AsyncOAuth2Client.__mro__:
        package_name = base.__module__.split(".", 1)[0]
        if package_name in _OAUTH_HTTP_PACKAGES:
            return importlib.import_module(package_name)
    mro = [f"{cls.__module__}.{cls.__name__}" for cls in AsyncOAuth2Client.__mro__]
    raise RuntimeError(
        f"AsyncOAuth2Client is not a subclass of httpx.AsyncClient or httpx2.AsyncClient; mro={mro}"
    )


_AUTHLIB_HTTPX = _resolve_authlib_httpx_module()


def authlib_httpx_module() -> ModuleType:
    """Return the HTTP client module Authlib bound ``AsyncOAuth2Client`` to.

    ``httpx2`` types are separate from ``httpx`` types. Build timeouts, mock
    transports, responses, and ``HTTPError`` checks from this module. The
    ``[pydanticai]`` extra installs ``httpx2`` through ``genai-prices``.

    Example:
        >>> from asap.auth.oauth2 import authlib_httpx_module
        >>> http = authlib_httpx_module()
        >>> timeout = http.Timeout(10.0)
    """
    return _AUTHLIB_HTTPX


def _oauth_client_timeout() -> object:
    """Build the token-endpoint timeout from Authlib's bound HTTP stack."""
    timeout_cls = getattr(_AUTHLIB_HTTPX, "Timeout", None)
    if not isinstance(timeout_cls, type):
        raise RuntimeError(
            f"{_AUTHLIB_HTTPX.__name__}.Timeout is {timeout_cls!r}; expected a class"
        )
    return cast(object, timeout_cls(_TOKEN_ENDPOINT_TIMEOUT_SECONDS))


def _require_authlib_transport(transport: object) -> object:
    """Reject a transport from the HTTP stack Authlib did not bind."""
    base = getattr(_AUTHLIB_HTTPX, "AsyncBaseTransport", None)
    if isinstance(base, type) and isinstance(transport, base):
        return transport
    actual = f"{type(transport).__module__}.{type(transport).__qualname__}"
    expected = f"{_AUTHLIB_HTTPX.__name__}.AsyncBaseTransport"
    raise TypeError(
        f"OAuth transport {actual} is not a {expected}; "
        f"Authlib bound AsyncOAuth2Client to {_AUTHLIB_HTTPX.__name__}"
    )


class OAuth2ClientCredentials:
    """OAuth2 client for client_credentials grant.

    Obtains access tokens from any standard OAuth2 token endpoint
    using the client_credentials flow (machine-to-machine auth).

    Internally uses Authlib's AsyncOAuth2Client (see ADR-12). Token requests
    use the HTTP stack that client is bound to (``authlib_httpx_module()``):
    ``httpx``, or ``httpx2`` when that package is installed. Timeouts and
    transports come from that module. ``transport`` is annotated as ``Any``
    because those stacks do not share ``AsyncBaseTransport``. The runtime
    check accepts only the bound stack and raises ``TypeError`` otherwise.

    Example:
        >>> client = OAuth2ClientCredentials(
        ...     client_id="my-client",
        ...     client_secret="secret",
        ...     token_url="https://auth.example.com/oauth/token",
        ... )
        >>> token = await client.get_access_token()
        >>> headers = {"Authorization": f"{token.token_type} {token.access_token}"}
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        *,
        scope: Optional[str] = None,
        transport: Any = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scope = scope
        if transport is not None:
            transport = _require_authlib_transport(transport)
        self._transport = transport
        self._cached_token: Optional[Token] = None
        self._refresh_lock = asyncio.Lock()

    async def get_valid_token(self, scope: Optional[str] = None) -> Token:
        """Return a valid token, using cache if not expired or refreshing if near expiry.

        Reuses the cached token when it is still valid. Refreshes 30 seconds
        before actual expiry to prevent race conditions (see TOKEN_REFRESH_BUFFER_SECONDS).
        Args:
            scope: Optional scopes to request (overrides constructor scope).

        Returns:
            Token that is valid for at least TOKEN_REFRESH_BUFFER_SECONDS.

        Raises:
            authlib.integrations.base_client.OAuthError: Token endpoint
                rejected the request, including HTTP 401 ``invalid_client``.
                Catching only ``HTTPStatusError`` misses this path.
            httpx.HTTPError: Network or transport failure from the HTTP stack
                Authlib bound (``authlib_httpx_module()``). The class is
                ``httpx.HTTPError``, or ``httpx2.HTTPError`` when ``httpx2``
                is installed. ``ConnectError`` is the connection case.
        """
        if self._cached_token is not None and not self._cached_token.is_expired(
            buffer_seconds=TOKEN_REFRESH_BUFFER_SECONDS
        ):
            return self._cached_token
        async with self._refresh_lock:
            if self._cached_token is not None and not self._cached_token.is_expired(
                buffer_seconds=TOKEN_REFRESH_BUFFER_SECONDS
            ):
                return self._cached_token
            token = await self.get_access_token(scope=scope)
            self._cached_token = token
            return token

    async def get_access_token(self, scope: Optional[str] = None) -> Token:
        """Obtain a new access token from the token endpoint.

        Args:
            scope: Optional scopes to request (overrides constructor scope).

        Returns:
            Token with access_token, expires_at, and token_type.

        Raises:
            authlib.integrations.base_client.OAuthError: Token endpoint
                rejected the request, including HTTP 401 ``invalid_client``.
                Catching only ``HTTPStatusError`` misses this path.
            httpx.HTTPError: Network or transport failure from the HTTP stack
                Authlib bound (``authlib_httpx_module()``). The class is
                ``httpx.HTTPError``, or ``httpx2.HTTPError`` when ``httpx2``
                is installed. ``ConnectError`` is the connection case.
        """
        requested_scope = scope or self._scope

        kwargs: dict[str, Any] = {"timeout": _oauth_client_timeout()}
        if self._transport is not None:
            kwargs["transport"] = self._transport

        async with AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            scope=requested_scope,
            **kwargs,
        ) as client:
            raw_token: dict[str, Any] = await client.fetch_token(
                url=self._token_url,
                grant_type="client_credentials",
            )

        token = _parse_token_response(raw_token)
        expires_in = token.expires_at - int(time.time())
        logger.info(
            "asap.oauth2.token_acquired",
            token_endpoint=self._token_url,
            expires_in=expires_in,
        )
        return token
