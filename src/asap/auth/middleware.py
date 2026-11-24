"""OAuth2 token validation middleware for ASAP Protocol.

Validates JWT Bearer tokens using JWKS (via joserfc), extracts claims
(sub, scope, exp), and returns 401 when invalid or 403 when scope is insufficient.
Supports Custom Claims identity binding (ADR-17).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from joserfc import jwk
from joserfc.errors import JoseError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Scope

from asap.auth._scope_parse import parse_scope
from asap.auth.jwks import JWKSValidator
from asap.observability import get_logger

logger = get_logger(__name__)

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
ERROR_AUTH_REQUIRED = "Authentication required"
ERROR_INVALID_TOKEN = "Invalid authentication token"  # nosec B105
ERROR_INSUFFICIENT_SCOPE = "Insufficient scope"
ERROR_IDENTITY_MISMATCH = "Identity mismatch: custom claim does not match agent manifest"

DEFAULT_CUSTOM_CLAIM = "https://github.com/adriannoes/asap-protocol/agent_id"
ENV_SUBJECT_MAP = "ASAP_AUTH_SUBJECT_MAP"
ENV_ISSUER = "ASAP_AUTH_ISSUER"
ENV_AUDIENCE = "ASAP_AUTH_AUDIENCE"


def _parse_subject_map() -> dict[str, str | list[str]]:
    """Parse ASAP_AUTH_SUBJECT_MAP env var (JSON dict: agent_id -> sub or list of subs).

    Returns empty dict on parse error or when env is unset.
    """
    raw = os.environ.get(ENV_SUBJECT_MAP)
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, (str, list))}


def parse_expected_audience_from_env(raw: str | None) -> str | list[str] | None:
    """Parse ``ASAP_AUTH_AUDIENCE`` env value (comma-separated list allowed).

    Example:
        >>> parse_expected_audience_from_env("urn:asap:agent:identity")
        'urn:asap:agent:identity'
        >>> parse_expected_audience_from_env(" a , b , c ")
        ['a', 'b', 'c']
    """
    if not raw or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return parts


@dataclass
class OAuth2Config:
    """Configuration for OAuth2 JWT validation on ASAP server routes.

    When passed to create_app(oauth2_config=...), OAuth2Middleware is applied
    to all requests under path_prefix (default /asap), validating Bearer JWTs
    using the provider's JWKS endpoint. Supports Custom Claims identity binding
    (ADR-17) when manifest_id is provided.

    Attributes:
        jwks_uri: URL of the JWKS endpoint (e.g. from OIDC discovery).
        required_scope: Optional scope that tokens must contain (e.g. "asap:execute").
        path_prefix: Path prefix to protect; default "/asap".
        jwks_fetcher: Optional async (uri) -> KeySet for tests; default fetches via httpx.
        manifest_id: Optional agent manifest id for identity binding; when set,
            validates custom claim or allowlist fallback. If None, identity binding
            is disabled.
        custom_claim: Optional JWT claim key for agent_id; when None, uses
            ASAP_AUTH_CUSTOM_CLAIM env var (default: DEFAULT_CUSTOM_CLAIM).
        expected_issuer: Optional expected JWT ``iss`` claim; uses ``ASAP_AUTH_ISSUER`` when unset.
        expected_audience: Optional expected JWT ``aud`` claim; uses ``ASAP_AUTH_AUDIENCE`` when unset.
    """

    jwks_uri: str
    required_scope: str | None = None
    path_prefix: str = "/asap"
    jwks_fetcher: Callable[[str], Awaitable[jwk.KeySet]] | None = None
    manifest_id: str | None = None
    custom_claim: str | None = None
    expected_issuer: str | None = None
    expected_audience: str | list[str] | None = None


@dataclass
class OAuth2Claims:
    """Claims extracted from a validated JWT.

    Attributes:
        sub: Subject (e.g. agent URN or user id).
        scope: List of scope strings (e.g. ["asap:execute", "asap:read"]).
        exp: Expiration timestamp (Unix).
    """

    sub: str
    scope: list[str]
    exp: int


def _get_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    return auth[7:].strip() or None


AGENT_IDENTITY_ROUTE_PREFIX = "/asap/agent/"

# Opt-in operator REST surfaces (#209): protected when create_app(require_operator_auth=True).
OPERATOR_API_PATH_PREFIXES: tuple[str, ...] = ("/usage", "/sla", "/audit")


def path_under_prefix(path: str, prefix: str) -> bool:
    """Return True if ``path`` is ``prefix`` or a subpath of it.

    Example:
        >>> path_under_prefix("/usage/export", "/usage")
        True
        >>> path_under_prefix("/usagex", "/usage")
        False
    """
    normalized = prefix.rstrip("/") or "/"
    if normalized == "/":
        return True
    return path == normalized or path.startswith(f"{normalized}/")


def _route_path(scope: Scope) -> str:
    """Return the path routing matches: ``scope["path"]`` without ``root_path``.

    A ``Mount``, a server ``--root-path`` or ``FastAPI.root_path`` puts the prefix
    in ``root_path`` and keeps it at the front of ``path``. ``root_path`` is removed
    only when it is a whole-segment prefix of ``path``; otherwise ``path`` is
    returned unchanged (a path that was never prefixed). This is the rule
    Starlette's router applies, so access checks see the path that gets routed.
    """
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if not root_path or not path.startswith(root_path):
        return path
    rest = path[len(root_path) :]
    if rest and not rest.startswith("/"):
        return path
    return rest


class OAuth2Middleware(BaseHTTPMiddleware):
    """Middleware that validates JWT Bearer tokens using JWKS.

    Extracts Authorization: Bearer <token>, validates the JWT signature
    with keys from jwks_uri, checks exp, and optionally enforces a
    required scope. Sets request.state.oauth2_claims on success.
    Skips ``/asap/agent/*`` (Host JWT, not IdP access tokens) and each path in
    ``exempt_paths``, compared exactly: list only a route that verifies its own
    bearer. Any other spelling of that path is still validated here. Paths are
    matched as routed, without the ASGI ``root_path``.

    Returns 401 if the token is missing or invalid, 403 if scope is insufficient.
    """

    def __init__(
        self,
        app: Any,
        jwks_uri: str,
        *,
        required_scope: str | None = None,
        path_prefix: str | None = "/asap",
        extra_path_prefixes: tuple[str, ...] = (),
        jwks_fetcher: Callable[[str], Awaitable[jwk.KeySet]] | None = None,
        manifest_id: str | None = None,
        custom_claim: str | None = None,
        expected_issuer: str | None = None,
        expected_audience: str | list[str] | None = None,
        validator: JWKSValidator | None = None,
        exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(app)
        self._jwks_uri = jwks_uri
        self._exempt_paths = exempt_paths
        self._required_scope = required_scope
        self._path_prefix = path_prefix
        self._extra_path_prefixes = extra_path_prefixes
        self._manifest_id = manifest_id
        self._custom_claim = custom_claim
        self._custom_claim_key = self._custom_claim or os.environ.get(
            "ASAP_AUTH_CUSTOM_CLAIM", DEFAULT_CUSTOM_CLAIM
        )
        self._expected_issuer = expected_issuer or os.environ.get(ENV_ISSUER)
        self._expected_audience = expected_audience or parse_expected_audience_from_env(
            os.environ.get(ENV_AUDIENCE)
        )
        self._subject_map = _parse_subject_map()
        # One canonical JWKS cache lives on the shared JWKSValidator so the HTTP
        # middleware stack instance and the WS ``app.state`` instance do not
        # diverge (S0 #237). When callers hand us a pre-built validator (server
        # wiring shares ONE across both transports) we adopt it; otherwise we
        # build a private one, threading the test ``jwks_fetcher`` seam through.
        if validator is not None:
            self._validator = validator
        else:
            self._validator = JWKSValidator(
                jwks_uri,
                fetcher=jwks_fetcher,
                expected_issuer=self._expected_issuer,
                expected_audience=self._expected_audience,
                require_exp=False,
            )

    def _should_validate(self, path: str) -> bool:
        """Return True if this request path should be validated."""
        if path.startswith(AGENT_IDENTITY_ROUTE_PREFIX) or path in self._exempt_paths:
            return False
        # path_prefix=None keeps the historic "validate everything" semantics.
        if self._path_prefix is None:
            return True
        if path_under_prefix(path, self._path_prefix):
            return True
        return any(path_under_prefix(path, prefix) for prefix in self._extra_path_prefixes)

    def _enforces_global_scope(self, path: str) -> bool:
        """Return True if the global ``required_scope`` should apply to ``path``.

        Applies to ``path_prefix`` matches (and the historic ``path_prefix=None``
        "validate everything" mode). Paths matched only via ``extra_path_prefixes``
        (operator APIs) are excluded: their scope is enforced by the router
        ``Depends(require_scope(...))`` so the global scope must not compound (#209).
        """
        if self._path_prefix is None:
            return True
        return path_under_prefix(path, self._path_prefix)

    def _validate_scope(self, scope_list: list[str]) -> bool:
        """Check if required_scope is present in scope list."""
        if self._required_scope is None:
            return True
        return self._required_scope in scope_list

    def _validate_identity_binding(
        self, claims: dict[str, Any], sub: str
    ) -> tuple[bool, str | None]:
        """Validate JWT identity binding via custom claim or allowlist (ADR-17).

        Returns ``(True, None)`` on success and ``(False, detail)`` on mismatch.
        Allowlist hits are logged here (``identity_via_allowlist``) rather than
        surfaced as a separate flag to the caller, so the result is a flat
        ``(ok, error_detail)`` pair.
        """
        if self._manifest_id is None:
            return True, None

        claim_key = self._custom_claim_key
        claim_value = claims.get(claim_key)

        if claim_value is not None:
            agent_id = str(claim_value).strip()
            if agent_id != self._manifest_id:
                return (
                    False,
                    f"Custom claim {claim_key!r} has {agent_id!r}, expected {self._manifest_id!r}",
                )
            return True, None

        allowed = self._subject_map.get(self._manifest_id)
        if allowed is not None:
            if isinstance(allowed, str) and allowed == sub:
                logger.warning(
                    "asap.oauth2.identity_via_allowlist",
                    manifest_id=self._manifest_id,
                    sub=sub,
                )
                return True, None
            if isinstance(allowed, list) and sub in allowed:
                logger.warning(
                    "asap.oauth2.identity_via_allowlist",
                    manifest_id=self._manifest_id,
                    sub=sub,
                )
                return True, None

        return False, "Missing required identity claim"

    async def _validate_token_claims(self, token: str) -> dict[str, Any]:
        """Decode+validate the JWT via the shared validator with key-rotation retry.

        Fetches the JWKS (cached on the validator), decodes the token, and on a
        ``JoseError`` (e.g. unknown ``kid`` from key rotation) invalidates the
        cache and retries once. Network errors surface as ``httpx.HTTPError``
        for the caller to map to a 503 response.
        """
        key_set = await self._validator.fetch_keys(self._jwks_uri)
        try:
            return self._validator.validate_jwt(token, key_set)
        except JoseError:
            await self._validator.invalidate_cache()
            key_set = await self._validator.fetch_keys(self._jwks_uri)
            return self._validator.validate_jwt(token, key_set)

    async def validate_bearer_token(
        self, token: str, *, path: str = "", enforce_required_scope: bool = True
    ) -> tuple[OAuth2Claims | None, JSONResponse | None]:
        """Run the full JWKS validation pipeline for a single Bearer token.

        Shared by the HTTP middleware ``dispatch`` and the WebSocket acceptance
        path so both transports enforce identical OAuth2 semantics (B4/BUG #4).
        Returns ``(claims, None)`` on success or ``(None, error_response)`` on
        failure. ``path`` is used only for log context.

        ``enforce_required_scope`` gates the global ``required_scope`` check. It
        is set to False for ``extra_path_prefixes`` (operator APIs) whose scope is
        enforced by their own router ``Depends(require_scope(...))`` (#209), so the
        global scope does not compound with the per-route scope.
        """
        try:
            claims = await self._validate_token_claims(token)
        except httpx.HTTPError as e:
            logger.error(
                "asap.oauth2.jwks_fetch_failed",
                path=path,
                jwks_uri=self._jwks_uri,
                error=str(e),
            )
            return None, JSONResponse(
                status_code=503,
                content={"detail": "Authentication service unavailable"},
            )
        except JoseError as e:
            logger.warning("asap.oauth2.invalid_token", path=path, error=str(e))
            return None, JSONResponse(
                status_code=HTTP_UNAUTHORIZED,
                content={"detail": ERROR_INVALID_TOKEN},
                headers={"WWW-Authenticate": "Bearer"},
            )

        exp = claims.get("exp")
        exp_ts = 0
        if exp is not None:
            if not isinstance(exp, (int, float)):
                logger.warning("asap.oauth2.invalid_exp_type", path=path)
                return None, JSONResponse(
                    status_code=HTTP_UNAUTHORIZED,
                    content={"detail": ERROR_INVALID_TOKEN},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                exp_ts = int(exp)
            except (TypeError, ValueError):
                logger.warning("asap.oauth2.invalid_exp_type", path=path)
                return None, JSONResponse(
                    status_code=HTTP_UNAUTHORIZED,
                    content={"detail": ERROR_INVALID_TOKEN},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        if exp is not None and exp_ts < time.time():
            logger.warning("asap.oauth2.expired_token", path=path)
            return None, JSONResponse(
                status_code=HTTP_UNAUTHORIZED,
                content={"detail": ERROR_INVALID_TOKEN},
                headers={"WWW-Authenticate": "Bearer"},
            )

        sub = claims.get("sub")
        if not sub or not isinstance(sub, str):
            logger.warning("asap.oauth2.missing_sub", path=path)
            return None, JSONResponse(
                status_code=HTTP_UNAUTHORIZED,
                content={"detail": ERROR_INVALID_TOKEN},
                headers={"WWW-Authenticate": "Bearer"},
            )

        scope_list = parse_scope(claims.get("scope"))
        if enforce_required_scope and not self._validate_scope(scope_list):
            logger.warning(
                "asap.oauth2.insufficient_scope",
                path=path,
                required=self._required_scope,
                has_scopes=scope_list,
            )
            return None, JSONResponse(
                status_code=HTTP_FORBIDDEN,
                content={"detail": ERROR_INSUFFICIENT_SCOPE},
            )

        success, err_detail = self._validate_identity_binding(claims, sub)
        if not success:
            logger.warning(
                "asap.oauth2.identity_mismatch",
                path=path,
                detail=err_detail,
            )
            detail_msg = (
                f"{ERROR_IDENTITY_MISMATCH} (expected: {self._manifest_id})"
                if self._manifest_id
                else ERROR_IDENTITY_MISMATCH
            )
            return None, JSONResponse(
                status_code=HTTP_FORBIDDEN,
                content={"detail": detail_msg},
            )

        return OAuth2Claims(sub=sub, scope=scope_list, exp=exp_ts), None

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Validate JWT and optionally scope; return 401/403 or pass to next."""
        route_path = _route_path(request.scope)
        if not self._should_validate(route_path):
            return await call_next(request)

        token = _get_bearer_token(request)
        if not token:
            logger.warning("asap.oauth2.missing_token", path=request.url.path)
            return JSONResponse(
                status_code=HTTP_UNAUTHORIZED,
                content={"detail": ERROR_AUTH_REQUIRED},
                headers={"WWW-Authenticate": "Bearer"},
            )

        claims, error = await self.validate_bearer_token(
            token,
            path=request.url.path,
            enforce_required_scope=self._enforces_global_scope(route_path),
        )
        if error is not None:
            return error
        # validate_bearer_token returns None claims only with an error response;
        # the guard above ensures claims is set on the success path.
        request.state.oauth2_claims = claims
        return await call_next(request)
