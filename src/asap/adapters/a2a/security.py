"""Security scheme models of the A2A v1.0 Agent Card.

``SecurityScheme`` is a wrapper with exactly one key, such as
``{"httpAuthSecurityScheme": {"scheme": "Bearer"}}``; there is no ``type``
field (that is the v0.3 shape). ``OAuthFlows`` holds exactly one flow. The
``implicit`` and ``password`` flows are deprecated in the proto; they are
parsed on read. A security requirement maps scheme names to scopes:
``{"schemes": {"oauth": {"list": ["agent:read"]}}}``; ``list`` may be absent.

The ``securityRequirements`` array lists alternatives (OR). Inside one entry,
every named scheme is required (AND), as in OpenAPI. A2A v1.0.1 does not say;
this is the reading the ASAP guide states.
"""

from __future__ import annotations

from typing import ClassVar, Self

from pydantic import Field, StrictBool, model_validator

from asap.adapters.a2a._base import A2AModel, NonEmptyStr


def _require_exactly_one_member(model: A2AModel) -> None:
    """Raise ``ValueError`` unless exactly one field of a proto ``oneof`` wrapper is set."""
    fields = type(model).model_fields
    present = [fields[name].alias for name in fields if getattr(model, name) is not None]
    if len(present) != 1:
        allowed = sorted(str(info.alias) for info in fields.values())
        raise ValueError(
            f"{type(model).__name__} must hold exactly one of {allowed}, got {present or 'none'}"
        )


class AuthorizationCodeOAuthFlow(A2AModel):
    """OAuth 2.0 authorization code flow; ``pkce_required`` false is not dumped."""

    _omit_when_false: ClassVar[frozenset[str]] = frozenset({"pkce_required"})

    authorization_url: NonEmptyStr
    token_url: NonEmptyStr
    refresh_url: str | None = None
    scopes: dict[str, str] = Field(min_length=1)
    pkce_required: StrictBool = False


class ClientCredentialsOAuthFlow(A2AModel):
    """OAuth 2.0 client credentials flow."""

    token_url: NonEmptyStr
    refresh_url: str | None = None
    scopes: dict[str, str] = Field(min_length=1)


class DeviceCodeOAuthFlow(A2AModel):
    """OAuth 2.0 device authorization flow (RFC 8628)."""

    device_authorization_url: NonEmptyStr
    token_url: NonEmptyStr
    refresh_url: str | None = None
    scopes: dict[str, str] = Field(min_length=1)


class ImplicitOAuthFlow(A2AModel):
    """Deprecated in the proto. Parsed on read; the proto marks no field required."""

    authorization_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] | None = None


class PasswordOAuthFlow(A2AModel):
    """Deprecated in the proto. Parsed on read; the proto marks no field required."""

    token_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] | None = None


class OAuthFlows(A2AModel):
    """The flow of an OAuth 2.0 scheme: exactly one of the five members is set."""

    authorization_code: AuthorizationCodeOAuthFlow | None = None
    client_credentials: ClientCredentialsOAuthFlow | None = None
    device_code: DeviceCodeOAuthFlow | None = None
    implicit: ImplicitOAuthFlow | None = None
    password: PasswordOAuthFlow | None = None

    @model_validator(mode="after")
    def _check_one_flow(self) -> Self:
        _require_exactly_one_member(self)
        return self


class APIKeySecurityScheme(A2AModel):
    """An API key sent in a header, query parameter, or cookie (``location``)."""

    description: str | None = None
    location: NonEmptyStr
    name: NonEmptyStr


class HTTPAuthSecurityScheme(A2AModel):
    """HTTP authentication (RFC 9110), for example ``scheme="Bearer"``."""

    description: str | None = None
    scheme: NonEmptyStr
    bearer_format: str | None = None


class OAuth2SecurityScheme(A2AModel):
    """OAuth 2.0; ``oauth2_metadata_url`` points to RFC 8414 server metadata."""

    description: str | None = None
    flows: OAuthFlows
    oauth2_metadata_url: str | None = None


class OpenIdConnectSecurityScheme(A2AModel):
    """OpenID Connect; ``open_id_connect_url`` is the discovery document."""

    description: str | None = None
    open_id_connect_url: NonEmptyStr


class MutualTlsSecurityScheme(A2AModel):
    """Mutual TLS. A card signature covers it only when ``description`` is set."""

    description: str | None = None


class SecurityScheme(A2AModel):
    """One entry of ``securitySchemes``: exactly one of the five wrapper keys is set.

    Example:
        >>> SecurityScheme(
        ...     http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")
        ... ).model_dump(mode="json")
        {'httpAuthSecurityScheme': {'scheme': 'Bearer'}}
    """

    api_key_security_scheme: APIKeySecurityScheme | None = None
    http_auth_security_scheme: HTTPAuthSecurityScheme | None = None
    oauth2_security_scheme: OAuth2SecurityScheme | None = None
    open_id_connect_security_scheme: OpenIdConnectSecurityScheme | None = None
    mtls_security_scheme: MutualTlsSecurityScheme | None = None

    @model_validator(mode="after")
    def _check_one_scheme(self) -> Self:
        _require_exactly_one_member(self)
        return self


class StringList(A2AModel):
    """The scopes of one scheme in a requirement. JSON name ``list``; may be absent."""

    scopes: list[str] | None = Field(default=None, alias="list")


class SecurityRequirement(A2AModel):
    """One alternative of ``securityRequirements``: scheme name → required scopes."""

    schemes: dict[str, StringList] | None = None
