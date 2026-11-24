"""Constraint checks fail closed: a check that cannot be evaluated is a violation."""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from asap.auth.capabilities import OPERATOR_KEYS, ConstraintViolation, validate_constraints

_NAN = float("nan")
_INF = float("inf")

# One sample per JSON type on each side, plus the non-finite floats json.loads returns.
_JSON_SAMPLES: list[Any] = [None, True, 5, 5.5, _NAN, _INF, -_INF, "abc", ["abc", 5], {"abc": 1}]

# Operator, constraint value, argument: mixes that raised TypeError instead of a violation.
_UNEVALUABLE: list[tuple[str, Any, Any]] = [
    *((op, bound, 5) for op in ("max", "min") for bound in (None, "10", [10], {"v": 10})),
    *((op, bound, "a") for op in ("in", "not_in") for bound in (None, True, 5, 1.5)),
    *(
        (op, "/srv/public", arg)
        for op in ("in", "not_in")
        for arg in (5, True, 1.5, ["/srv"], {"p": 1})
    ),
    *((op, {"a": 1}, arg) for op in ("in", "not_in") for arg in (["a"], {"a": 1})),
]


@pytest.mark.parametrize(
    ("operator", "constraint", "argument"),
    # A null argument counts as missing ("required"), so it never reaches the operator.
    itertools.product(sorted(OPERATOR_KEYS), _JSON_SAMPLES, _JSON_SAMPLES[1:]),
)
def test_every_json_type_mix_returns_without_raising(
    operator: str, constraint: Any, argument: Any
) -> None:
    violations = validate_constraints({"f": {operator: constraint}}, {"f": argument})

    assert all(isinstance(v, ConstraintViolation) for v in violations)
    assert all((v.field, v.operator) == ("f", operator) for v in violations)


@pytest.mark.parametrize(("operator", "constraint", "argument"), _UNEVALUABLE)
def test_unevaluable_mix_is_a_violation(operator: str, constraint: Any, argument: Any) -> None:
    violations = validate_constraints({"f": {operator: constraint}}, {"f": argument})

    assert [(v.field, v.operator) for v in violations] == [("f", operator)]
    assert repr(constraint) not in violations[0].message
    assert repr(argument) not in violations[0].message


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
@pytest.mark.parametrize("operator", ["max", "min"])
def test_non_finite_argument_violates_numeric_bound(operator: str, raw: str) -> None:
    arguments = json.loads(f'{{"amount": {raw}}}')

    violations = validate_constraints({"amount": {operator: 10}}, arguments)

    assert [(v.field, v.operator) for v in violations] == [("amount", operator)]


@pytest.mark.parametrize("bound", [_NAN, _INF, -_INF])
@pytest.mark.parametrize("operator", ["max", "min"])
def test_non_finite_bound_is_a_violation(operator: str, bound: float) -> None:
    violations = validate_constraints({"amount": {operator: bound}}, {"amount": 5})

    assert [(v.field, v.operator) for v in violations] == [("amount", operator)]


@pytest.mark.parametrize(
    ("constraints", "arguments"),
    [
        ({"n": {"max": 1}}, {"n": True}),  # a boolean counts as a number
        ({"n": {"max": True, "min": False}}, {"n": 1}),
        ({"n": {"min": 1}}, {"n": 10**400}),  # a large int is finite
        ({"n": 1}, {"n": 1.0}),  # 1 equals 1.0
        ({"n": {"in": [1]}}, {"n": 1.0}),
        ({"p": {"in": "/srv/public"}}, {"p": "/srv"}),  # substring test
        ({"p": {"in": {"a": 1}}}, {"p": "a"}),  # key test
        ({"n": {"max": 10, "note": "ignored"}}, {"n": 5}),  # unknown keys ignored
    ],
)
def test_behavior_that_worked_before_is_unchanged(
    constraints: dict[str, Any], arguments: dict[str, Any]
) -> None:
    assert validate_constraints(constraints, arguments) == []
