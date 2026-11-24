"""Shared constraint vectors for ``validate_constraints`` (COMPAT-002).

``tests/fixtures/interop/constraints.json`` pins what the Python check does, so
the TypeScript port can match it. The last two tests cover the
fail-closed rule for inputs that portable JSON cannot carry (``NaN``) or that
no operator can compare: each one is a violation, never a pass or an exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asap.auth.capabilities import validate_constraints

VECTOR_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "interop" / "constraints.json"
VECTORS: list[dict[str, Any]] = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_constraint_vector(vector: dict[str, Any]) -> None:
    found = validate_constraints(vector["constraints"], vector["arguments"])
    expected = vector["violations"]
    assert sorted((v.field, v.operator) for v in found) == sorted(
        (v["field"], v["operator"]) for v in expected
    )


def test_nan_argument_violates_max_and_min() -> None:
    """A non-finite number fails both bounds.

    ``json.loads`` (and so ``Request.json()``) accepts ``NaN`` in a call body.
    """
    arguments = json.loads('{"amount": NaN}')
    found = validate_constraints({"amount": {"max": 100, "min": 0}}, arguments)
    assert sorted((v.field, v.operator) for v in found) == [("amount", "max"), ("amount", "min")]


@pytest.mark.parametrize(
    ("constraints", "arguments", "operator"),
    [
        ({"amount": {"max": "100"}}, {"amount": 5}, "max"),
        ({"env": {"in": None}}, {"env": "dev"}, "in"),
        ({"path": {"in": "/srv/public"}}, {"path": 5}, "in"),
        ({"tier": {"in": {"gold": 1}}}, {"tier": ["gold"]}, "in"),
    ],
)
def test_operands_that_cannot_be_compared_are_a_violation(
    constraints: dict[str, Any], arguments: dict[str, Any], operator: str
) -> None:
    found = validate_constraints(constraints, arguments)
    field = next(iter(constraints))
    assert [(v.field, v.operator) for v in found] == [(field, operator)]
