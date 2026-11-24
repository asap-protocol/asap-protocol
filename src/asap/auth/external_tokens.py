"""External OAuth access tokens: the issuer config (v2.6.0).

COMPAT-001 of ``product/prd/prd-interop.md``: a
bearer token from a configured issuer becomes an ephemeral principal for one
request. The config is strict: at least one issuer, a required audience per
issuer, and no "accept any issuer" mode. Every URL in it must pass
:func:`asap.utils.guarded_fetch.validate_guarded_url` unless that issuer sets
``allow_private_hosts`` for local development.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from joserfc import jwk
from joserfc.errors import JoseError
from pydantic import Field, SecretStr, field_validator, model_validator

from asap.auth.jwks import issuer_matches_expected
from asap.models.base import ASAPBaseModel
from asap.utils.guarded_fetch import GuardedFetchError, validate_guarded_url


class JwksUriKeySource(ASAPBaseModel):
    """Verify JWT access tokens with keys fetched from ``jwks_uri``."""

    kind: Literal["jwks_uri"]
    jwks_uri: str


class OidcDiscoveryKeySource(ASAPBaseModel):
    """Find ``jwks_uri`` in the issuer's ``/.well-known/openid-configuration``."""

    kind: Literal["oidc_discovery"]


def _import_key_set(jwks: dict[str, Any]) -> jwk.KeySet:
    # joserfc types the input as a TypedDict; the JSON shape is checked on import.
    return jwk.KeySet.import_key_set(cast(jwk.KeySetSerialization, jwks))


class StaticJwksKeySource(ASAPBaseModel):
    """Verify with a JWKS held in the config: no fetch (local issuers, examples, CI)."""

    kind: Literal["static_jwks"]
    jwks: dict[str, Any]

    @field_validator("jwks")
    @classmethod
    def _public_asymmetric_keys_only(cls, jwks: dict[str, Any]) -> dict[str, Any]:
        try:
            key_set = _import_key_set(jwks)
        except (JoseError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"static_jwks is not a usable JWKS: {exc}") from exc
        for key in key_set.keys:
            if key.key_type == "oct" or key.is_private:
                raise ValueError(
                    f"static_jwks key kid={key.kid!r} has kty {key.key_type!r} and "
                    f"private={key.is_private}; expected a public RSA, EC, or OKP key"
                )
        return jwks

    def key_set(self) -> jwk.KeySet:
        """Return the configured keys as a joserfc ``KeySet``."""
        return _import_key_set(self.jwks)


ExternalKeySource = Annotated[
    JwksUriKeySource | OidcDiscoveryKeySource | StaticJwksKeySource,
    Field(discriminator="kind"),
]


class IntrospectionEndpoint(ASAPBaseModel):
    """RFC 7662 endpoint for opaque tokens, with this server's client credentials."""

    url: str
    client_id: str = Field(min_length=1)
    client_secret: SecretStr


def _check_url(url: str, *, allow_private_hosts: bool) -> None:
    try:
        validate_guarded_url(url, allow_private_hosts=allow_private_hosts)
    except GuardedFetchError as exc:
        raise ValueError(str(exc)) from exc


class ExternalIssuerConfig(ASAPBaseModel):
    """One allowlisted issuer: its identifier, the required audience, and how to verify.

    ``audience`` is this resource's identifier (RFC 8707); a token for another
    audience is refused, never forwarded.
    """

    issuer: str
    audience: str
    key_source: ExternalKeySource | None = None
    introspection: IntrospectionEndpoint | None = None
    allow_private_hosts: bool = False

    @field_validator("audience")
    @classmethod
    def _audience_not_blank(cls, audience: str) -> str:
        if not audience.strip() or audience != audience.strip():
            raise ValueError(f"audience {audience!r} must be non-blank with no outer spaces")
        return audience

    @model_validator(mode="after")
    def _one_verification_method_and_guarded_urls(self) -> ExternalIssuerConfig:
        if (self.key_source is None) == (self.introspection is None):
            raise ValueError(
                f"issuer {self.issuer!r} needs exactly one of key_source and introspection"
            )
        urls = [self.issuer]
        if isinstance(self.key_source, JwksUriKeySource):
            urls.append(self.key_source.jwks_uri)
        if self.introspection is not None:
            urls.append(self.introspection.url)
        for url in urls:
            _check_url(url, allow_private_hosts=self.allow_private_hosts)
        return self


class ExternalTokenConfig(ASAPBaseModel):
    """The issuer allowlist for external access tokens (at least one issuer)."""

    issuers: tuple[ExternalIssuerConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_issuers_and_one_introspection(self) -> ExternalTokenConfig:
        seen: set[str] = set()
        for entry in self.issuers:
            normalized = entry.issuer.rstrip("/")
            if normalized in seen:
                raise ValueError(f"duplicate issuer {entry.issuer!r}")
            seen.add(normalized)
        # An opaque token names no issuer, so it can go to one endpoint only;
        # sending it to several would hand it to issuers that did not mint it.
        introspected = [e.issuer for e in self.issuers if e.introspection is not None]
        if len(introspected) > 1:
            raise ValueError(f"at most one issuer may use introspection, got {introspected}")
        return self

    def issuer_for(self, iss: str) -> ExternalIssuerConfig | None:
        """Return the allowlisted issuer that matches ``iss`` (trailing slash ignored)."""
        for entry in self.issuers:
            if issuer_matches_expected({"iss": iss}, entry.issuer):
                return entry
        return None
