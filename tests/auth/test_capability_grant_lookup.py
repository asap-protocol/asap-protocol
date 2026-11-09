"""Capability lookup and constraint membership are exact.

Substring or case-insensitive checks would grant a neighboring capability,
agent, or argument value. These tests fail if equality becomes containment.
"""

from __future__ import annotations

from asap.auth.capabilities import CapabilityRegistry, validate_constraints


def test_check_grant_lookup_is_exact() -> None:
    """Only the registered agent id and capability name are allowed."""
    registry = CapabilityRegistry()
    registry.grant("agent-1", "file:read")

    allowed = registry.check_grant("agent-1", "file:read")
    assert allowed.allowed is True
    assert allowed.grant is not None

    denied_pairs = (
        ("agent-1", "file:reader"),
        ("agent-1", "file:rea"),
        ("agent-1", "File:Read"),
        ("agent-1-extra", "file:read"),
        ("agent", "file:read"),
        ("Agent-1", "file:read"),
    )
    for agent_id, capability in denied_pairs:
        result = registry.check_grant(agent_id, capability)
        assert result.allowed is False
        assert result.grant is None
        assert result.violations == []


def test_constraint_equality_and_membership_are_exact() -> None:
    """Exact-match and list membership do not accept case or neighbor values."""
    case_miss = validate_constraints({"mode": "read"}, {"mode": "Read"})
    assert len(case_miss) == 1
    assert case_miss[0].operator == "eq"
    assert case_miss[0].message == "mode: expected 'read', got 'Read'"

    neighbor = validate_constraints({"mode": "read"}, {"mode": "reader"})
    assert len(neighbor) == 1
    assert neighbor[0].operator == "eq"
    assert neighbor[0].message == "mode: expected 'read', got 'reader'"

    contained = validate_constraints({"role": {"in": ["admin"]}}, {"role": "administrator"})
    assert len(contained) == 1
    assert contained[0].operator == "in"
    assert contained[0].message == ("role: 'administrator' not in allowed values ['admin']")

    empty_list = validate_constraints({"role": {"in": []}}, {"role": "admin"})
    assert len(empty_list) == 1
    assert empty_list[0].operator == "in"
    assert empty_list[0].message == "role: 'admin' not in allowed values []"

    not_forbidden = validate_constraints(
        {"role": {"not_in": ["admin"]}},
        {"role": "administrator"},
    )
    assert not_forbidden == []
