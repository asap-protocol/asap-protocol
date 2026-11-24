"""Security scheme models of the A2A v1.0 Agent Card.

``OAuthFlows`` holds exactly one flow. The ``implicit`` and ``password`` flows
are deprecated in the proto; they are parsed on read.
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
