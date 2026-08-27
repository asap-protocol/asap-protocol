"""ASAP Protocol Authentication Layer.

This module provides OAuth2/OIDC authentication for agent-to-agent communication:
- OAuth2 client for obtaining access tokens (client_credentials, authorization_code)
- Token validation middleware for protecting ASAP endpoints
- Token introspection for opaque tokens (RFC 7662)

Public exports:
    Token: OAuth2 access token model
    OAuth2ClientCredentials: Client for client_credentials grant
    OAuth2Middleware: JWT validation middleware (JWKS)
    OAuth2Config: Config for enabling OAuth2 on the server (create_app)
    OAuth2Claims: JWT claims (sub, scope, exp)
    require_scope: FastAPI dependency factory for scope-based authz
    SCOPE_READ, SCOPE_EXECUTE, SCOPE_ADMIN: Scope constants
    TokenIntrospector: RFC 7662 token introspection client
    TokenInfo: Introspection response model
    OIDCDiscovery: OIDC discovery client
    OIDCConfig: OIDC provider configuration model
    JWKSValidator: JWKS fetcher and JWT validator with key rotation support
    HostIdentity, AgentSession, HostStore, AgentStore, JWT helpers: per-runtime identity (Ed25519)
"""

from asap.auth.agent_jwt import (
    HOST_REVOKED_ERROR,
    create_agent_jwt,
    create_host_jwt,
    verify_agent_jwt,
    verify_host_jwt,
)
from asap.auth.identity import (
    AgentSession,
    AgentStore,
    HostIdentity,
    HostStore,
    RevokedAgentOverwriteError,
    jwk_thumbprint_sha256,
)
from asap.auth.introspection import TokenInfo, TokenIntrospector
from asap.auth.jwks import JWKSValidator
from asap.auth.middleware import OAuth2Claims, OAuth2Config, OAuth2Middleware
from asap.auth.oauth2 import OAuth2ClientCredentials, Token
from asap.auth.oidc import OIDCConfig, OIDCDiscovery
from asap.auth.scopes import SCOPE_ADMIN, SCOPE_EXECUTE, SCOPE_READ, require_scope

__all__ = [
    "AgentSession",
    "AgentStore",
    "HOST_REVOKED_ERROR",
    "HostIdentity",
    "HostStore",
    "JWKSValidator",
    "OIDCConfig",
    "OIDCDiscovery",
    "OAuth2Claims",
    "OAuth2ClientCredentials",
    "OAuth2Config",
    "OAuth2Middleware",
    "RevokedAgentOverwriteError",
    "SCOPE_ADMIN",
    "SCOPE_EXECUTE",
    "SCOPE_READ",
    "Token",
    "TokenInfo",
    "TokenIntrospector",
    "create_agent_jwt",
    "create_host_jwt",
    "jwk_thumbprint_sha256",
    "require_scope",
    "verify_agent_jwt",
    "verify_host_jwt",
]
