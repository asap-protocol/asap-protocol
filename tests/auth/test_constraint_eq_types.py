"""Lock plain capability constraints to Python equality, not text coercion."""

from __future__ import annotations

from asap.auth.capabilities import validate_constraints


def test_eq_constraint_does_not_coerce_numbers_and_strings() -> None:
    """``10`` and ``\"10\"`` are different arguments for an exact constraint."""
    string_actual = validate_constraints({"limit": 10}, {"limit": "10"})
    assert len(string_actual) == 1
    assert string_actual[0].field == "limit"
    assert string_actual[0].operator == "eq"
    assert string_actual[0].expected == 10
    assert string_actual[0].actual == "10"
    assert string_actual[0].message == "limit: expected 10, got '10'"

    number_actual = validate_constraints({"limit": "10"}, {"limit": 10})
    assert len(number_actual) == 1
    assert number_actual[0].operator == "eq"
    assert number_actual[0].message == "limit: expected '10', got 10"

    assert validate_constraints({"limit": 10}, {"limit": 10}) == []
    assert validate_constraints({"limit": "10"}, {"limit": "10"}) == []
