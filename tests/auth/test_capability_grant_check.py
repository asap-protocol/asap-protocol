"""The per-grant check shared by the registry and the external-token path.

``check_capability_grant`` is the body of ``CapabilityRegistry.check_grant``,
taken out with no behavior change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from asap.auth.capabilities import (
    CapabilityGrant,
    CapabilityRegistry,
    GrantStatus,
    check_capability_grant,
)

_PAST = datetime.now(timezone.utc) - timedelta(minutes=1)
_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)
_PATH_IN_TMP = {"path": {"in": ["/tmp"]}}

# (status, constraints, expires_at, arguments, allowed, violation operators)
_CASES: list[tuple[GrantStatus, dict[str, Any] | None, datetime | None, Any, bool, list[str]]] = [
    ("active", None, None, None, True, []),
    ("active", None, _FUTURE, {"x": 1}, True, []),
    ("active", None, _PAST, None, False, []),
    ("pending", None, None, None, False, []),
    ("denied", None, None, None, False, []),
    ("active", _PATH_IN_TMP, None, {"path": "/tmp"}, True, []),
    ("active", _PATH_IN_TMP, None, {"path": "/etc"}, False, ["in"]),
    ("active", _PATH_IN_TMP, None, None, False, ["required"]),
    ("active", _PATH_IN_TMP, None, {}, False, ["required"]),
    ("pending", _PATH_IN_TMP, None, {"path": "/etc"}, False, []),
]


@pytest.mark.parametrize(
    ("status", "constraints", "expires_at", "arguments", "allowed", "ops"), _CASES
)
def test_shared_check_matches_the_registry(
    status: GrantStatus,
    constraints: dict[str, Any] | None,
    expires_at: datetime | None,
    arguments: Any,
    allowed: bool,
    ops: list[str],
) -> None:
    registry = CapabilityRegistry()
    grant = registry.grant(
        "agent-1", "file.read", status=status, constraints=constraints, expires_at=expires_at
    )

    shared = check_capability_grant(grant, arguments)

    assert shared == registry.check_grant("agent-1", "file.read", arguments)
    assert shared.allowed is allowed
    assert shared.grant is grant
    assert [v.operator for v in shared.violations] == ops


def test_no_grant_is_denied_without_violations() -> None:
    result = check_capability_grant(None, {"path": "/tmp"})
    assert (result.allowed, result.violations, result.grant) == (False, [], None)
    assert result == CapabilityRegistry().check_grant("agent-1", "file.read", {"path": "/tmp"})


def test_check_does_not_change_the_grant() -> None:
    grant = CapabilityGrant(capability="file.read", status="active", constraints=_PATH_IN_TMP)
    before = grant.model_dump()
    check_capability_grant(grant, {"path": "/etc"})
    assert grant.model_dump() == before
