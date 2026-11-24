"""Rules for the security part of a card that ASAP generates.

The A2A SDK canonical form drops empty values before it signs, so some
security data is never covered by a card signature
(``docs/guides/interoperability.md``, "Signed Agent Cards"): an mTLS scheme with
no description, and a requirement that lists no scope for a scheme. A card
that ASAP generates never holds either one.
"""

from __future__ import annotations

from asap.adapters.a2a.errors import A2AMappingInputError
from asap.adapters.a2a.security import (
    MutualTlsSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
)

MTLS_DEFAULT_DESCRIPTION = "Mutual TLS client authentication"
"""Description given to an mTLS scheme that has none, so a signature covers the scheme."""


def prepare_card_security_schemes(
    schemes: dict[str, SecurityScheme] | None,
) -> dict[str, SecurityScheme] | None:
    """Return *schemes* ready for a card: mTLS described, no deprecated flow, empty is ``None``."""
    if not schemes:
        return None
    for name, scheme in schemes.items():
        flows = scheme.oauth2_security_scheme.flows if scheme.oauth2_security_scheme else None
        if flows is not None and (flows.implicit is not None or flows.password is not None):
            raise A2AMappingInputError(
                "security_schemes",
                f"security scheme {name!r} uses a deprecated OAuth flow (implicit or "
                "password); use authorizationCode, clientCredentials, or deviceCode",
            )
    return {name: _with_mtls_description(scheme) for name, scheme in schemes.items()}


def check_card_security_requirements(
    requirements: list[SecurityRequirement] | None,
    schemes: dict[str, SecurityScheme] | None,
) -> list[SecurityRequirement] | None:
    """Return *requirements* if a card signature covers every one; empty is ``None``.

    Raises:
        A2AMappingInputError: A requirement names no scheme, names a scheme that
            *schemes* does not declare, or lists no scope for a scheme.
    """
    if not requirements:
        return None
    declared = set(schemes or {})
    for index, requirement in enumerate(requirements):
        named = requirement.schemes or {}
        if not named:
            _refuse_requirement(index, "names no scheme")
        for name, scopes in named.items():
            if name not in declared:
                _refuse_requirement(index, f"names scheme {name!r}, which is not declared")
            if not scopes.scopes or not all(scopes.scopes):
                _refuse_requirement(index, f"lists no scope (or an empty one) for {name!r}")
    return requirements


def _refuse_requirement(index: int, problem: str) -> None:
    raise A2AMappingInputError(
        "security_requirements",
        f"security_requirements[{index}] {problem}: a card signature does not cover a "
        "requirement without a declared scheme and at least one non-empty scope (or role) "
        "for each scheme it names",
    )


def _with_mtls_description(scheme: SecurityScheme) -> SecurityScheme:
    mtls = scheme.mtls_security_scheme
    if mtls is None or mtls.description:
        return scheme
    described = MutualTlsSecurityScheme(description=MTLS_DEFAULT_DESCRIPTION)
    return SecurityScheme(mtls_security_scheme=described)
