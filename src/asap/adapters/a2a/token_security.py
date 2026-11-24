"""Card security that states what an external-token config enforces (v2.6.0).

How an issuer becomes a card security scheme (see
``docs/guides/interoperability.md``):

- Each issuer is one ``openIdConnectSecurityScheme`` named by the issuer
  identifier. ``oauth2SecurityScheme`` is never used: its flows need a token
  URL and scopes that :class:`ExternalTokenConfig` does not hold.
- Each scope rule (:class:`asap.auth.external_grants.ExternalGrantRule`) whose
  capability is a card skill id is one alternative on that skill: the skill
  ``id`` is the capability name. The card-level list is the union of them.
- Claim rules cannot be written as an OAuth scope, so they are not stated:
  the card may say less than the server accepts, never more.

Example:
    >>> security = external_token_card_security(config, ["search"])  # doctest: +SKIP
    >>> AgentCardInputs(..., security_schemes=security.security_schemes,
    ...     security_requirements=security.security_requirements,
    ...     skill_security_requirements=security.skill_security_requirements)  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from asap.adapters.a2a.security import (
    OpenIdConnectSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from asap.auth.external_tokens import ExternalIssuerConfig, ExternalTokenConfig

_OIDC_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


@dataclass(frozen=True, slots=True)
class ExternalTokenCardSecurity:
    """The three security inputs of :class:`AgentCardInputs` for one external-token config."""

    security_schemes: dict[str, SecurityScheme]
    security_requirements: list[SecurityRequirement]
    skill_security_requirements: dict[str, list[SecurityRequirement]]


def external_token_card_security(
    config: ExternalTokenConfig, skill_ids: Iterable[str]
) -> ExternalTokenCardSecurity:
    """Return the card security that states what *config* enforces for *skill_ids*.

    Scope rules for a capability that is not in *skill_ids* are left out; an
    issuer with no scope rule for a card skill still gets its scheme.

    Example:
        >>> external_token_card_security(config, ["search"]).security_schemes  # doctest: +SKIP
        {'https://idp.example': SecurityScheme(open_id_connect_security_scheme=...)}
    """
    schemes = {entry.issuer: _issuer_scheme(entry) for entry in config.issuers}
    by_skill: dict[str, list[SecurityRequirement]] = {}
    for skill_id in skill_ids:
        alternatives = [
            SecurityRequirement(schemes={entry.issuer: StringList(scopes=[scope])})
            for entry in config.issuers
            for scope in _scopes_for(entry, skill_id)
        ]
        if alternatives:
            by_skill[skill_id] = alternatives
    card_level: list[SecurityRequirement] = []
    for requirement in (r for alternatives in by_skill.values() for r in alternatives):
        if requirement not in card_level:
            card_level.append(requirement)
    return ExternalTokenCardSecurity(
        security_schemes=schemes,
        security_requirements=card_level,
        skill_security_requirements=by_skill,
    )


def _issuer_scheme(entry: ExternalIssuerConfig) -> SecurityScheme:
    # An A2A scheme has no audience field: the description is the only place for it.
    oidc = OpenIdConnectSecurityScheme(
        open_id_connect_url=entry.issuer.rstrip("/") + _OIDC_DISCOVERY_SUFFIX,
        description=f"Bearer access token from {entry.issuer} with audience {entry.audience}",
    )
    return SecurityScheme(open_id_connect_security_scheme=oidc)


def _scopes_for(entry: ExternalIssuerConfig, capability: str) -> list[str]:
    """The distinct scopes of *entry*'s scope rules for *capability*, in rule order."""
    scopes = [rule.scope for rule in entry.grant_rules if rule.capability == capability]
    return list(dict.fromkeys(scope for scope in scopes if scope is not None))
