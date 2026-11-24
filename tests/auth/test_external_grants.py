"""Grant mapper for external tokens (COMPAT-002).

The mapping cases live in ``tests/fixtures/interop/grant-mapping.json`` so the
TypeScript port runs the same cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from asap.auth.external_grants import ExternalGrantRule, map_external_grants
from asap.auth.external_tokens import ExternalIssuerConfig

VECTOR_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "interop" / "grant-mapping.json"
VECTORS: list[dict[str, Any]] = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_grant_mapping_vector(vector: dict[str, Any]) -> None:
    rules = [ExternalGrantRule.model_validate(rule) for rule in vector["rules"]]
    grants = map_external_grants(vector["claims"], rules)
    assert [{"capability": g.capability, "constraints": g.constraints} for g in grants] == (
        vector["expected_grants"]
    )
    assert all(grant.status == "active" for grant in grants)


@pytest.mark.parametrize(
    "rule",
    [
        {"capability": "file.read"},
        {"scope": "files:read", "claim": "groups", "value": "x", "capability": "file.read"},
        {"claim": "groups", "capability": "file.read"},
        {"scope": "files:read files:write", "capability": "file.read"},
        {"scope": "", "capability": "file.read"},
        {"claim": "scope", "value": "files:read", "capability": "file.read"},
        {"scope": "files:read", "capability": ""},
    ],
)
def test_rule_needs_exactly_one_well_formed_matcher(rule: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExternalGrantRule.model_validate(rule)


def _issuer_with_rules(rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "issuer": "https://issuer.example",
        "audience": "https://api.example/asap",
        "key_source": {"kind": "oidc_discovery"},
        "grant_rules": rules,
    }


def test_rules_that_give_one_capability_two_constraint_sets_are_an_error() -> None:
    rules = [
        {"scope": "files:read", "capability": "file.read", "constraints": {"path": "/a"}},
        {"scope": "files:all", "capability": "file.read"},
    ]
    with pytest.raises(ValidationError, match="file.read"):
        ExternalIssuerConfig.model_validate(_issuer_with_rules(rules))
