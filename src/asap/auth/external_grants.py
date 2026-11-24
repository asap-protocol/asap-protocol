"""Deny-by-default mapping from external token claims to capability grants (v2.6.0).

COMPAT-002 of ``product/prd/prd-interop.md``: each issuer has
static rules, and a rule gives one capability (with optional constraints) when
it matches one scope or one claim value. Nothing else grants anything: an
unmapped scope, a broad scope such as ``admin``, or an unknown claim gives no
grant. ``map_scopes_to_capabilities`` is not reused; it guesses from names.

Matching is exact and never converts types, so the TypeScript port gives the
same answers (``tests/fixtures/interop/grant-mapping.json``):

* a ``scope`` rule matches a token of the ``scope`` claim, a string split on
  single spaces (RFC 9068 §2.2.3, RFC 6749 §3.3); a non-string ``scope`` claim
  matches nothing;
* a ``claim`` rule matches when that top-level claim is the exact string
  ``value``, or a list that holds it. Numbers, booleans, and nested objects
  never match.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import Field, model_validator

from asap.auth.capabilities import CapabilityGrant
from asap.models.base import ASAPBaseModel

_SCOPE_CLAIM = "scope"


class ExternalGrantRule(ASAPBaseModel):
    """One mapping rule: a ``scope``, or a ``claim`` with a ``value``, gives ``capability``.

    Example:
        >>> ExternalGrantRule(scope="files:read", capability="file.read")
    """

    scope: str | None = None
    claim: str | None = None
    value: str | None = None
    capability: str = Field(min_length=1)
    constraints: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one_matcher(self) -> ExternalGrantRule:
        if self.scope is not None:
            single_token = bool(self.scope) and " " not in self.scope
            valid = self.claim is None and self.value is None and single_token
        else:
            valid = bool(self.claim) and self.claim != _SCOPE_CLAIM and self.value is not None
        if not valid:
            raise ValueError(
                f"grant rule for {self.capability!r} needs either a single-token scope, or a "
                f"claim other than 'scope' with a value; got scope={self.scope!r}, "
                f"claim={self.claim!r}, value={self.value!r}"
            )
        return self

    def matches(self, claims: Mapping[str, Any], scopes: frozenset[str]) -> bool:
        """Return True if this rule applies to the claims (``scopes`` already split)."""
        if self.scope is not None:
            return self.scope in scopes
        actual = claims.get(self.claim or "")
        if isinstance(actual, str):
            return actual == self.value
        if isinstance(actual, list):
            return any(isinstance(item, str) and item == self.value for item in actual)
        return False


def _scope_tokens(claims: Mapping[str, Any]) -> frozenset[str]:
    scope = claims.get(_SCOPE_CLAIM)
    if not isinstance(scope, str):
        return frozenset()
    return frozenset(token for token in scope.split(" ") if token)


def ensure_unambiguous_grant_rules(rules: Iterable[ExternalGrantRule]) -> None:
    """Refuse rules that give one capability two different constraint sets.

    With two such rules, a token that matches both would get constraints that
    depend on rule order. Rules that give the same capability and the same
    constraints are allowed.
    """
    constraints_by_capability: dict[str, dict[str, Any] | None] = {}
    for rule in rules:
        known = constraints_by_capability.setdefault(rule.capability, rule.constraints)
        if known != rule.constraints:
            raise ValueError(
                f"grant rules give capability {rule.capability!r} two constraint sets: "
                f"{known!r} and {rule.constraints!r}"
            )


def map_external_grants(
    claims: Mapping[str, Any], rules: Iterable[ExternalGrantRule]
) -> tuple[CapabilityGrant, ...]:
    """Return the active grants that ``rules`` give to validated ``claims``, by capability.

    Example:
        >>> rule = ExternalGrantRule(scope="files:read", capability="file.read")
        >>> [g.capability for g in map_external_grants({"scope": "files:read admin"}, [rule])]
        ['file.read']
    """
    scopes = _scope_tokens(claims)
    granted: dict[str, CapabilityGrant] = {}
    for rule in rules:
        if rule.capability not in granted and rule.matches(claims, scopes):
            granted[rule.capability] = CapabilityGrant(
                capability=rule.capability, status="active", constraints=rule.constraints
            )
    return tuple(granted[name] for name in sorted(granted))
