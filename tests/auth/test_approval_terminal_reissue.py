"""A finished or expired approval must not be handed out again.

``create_device_authorization`` and ``create_ciba_approval`` reuse a row only
while it is still pending and unexpired. Approved, denied, and expired rows
are replaced with a new pending challenge.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from asap.auth.approval import (
    InMemoryApprovalStore,
    create_ciba_approval,
    create_device_authorization,
)

_FIRST = "ABCD1234"
_SECOND = "EFGH5678"
_AGENT = "agent-reissue"


def _codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the next two device user codes deterministic."""
    pending: Iterator[str] = iter((_FIRST, _SECOND))
    monkeypatch.setattr("asap.auth.approval._generate_user_code", lambda: next(pending))


@pytest.mark.asyncio
async def test_expired_device_authorization_issues_a_new_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the deadline, the same agent gets a new pending user code."""
    _codes(monkeypatch)
    frozen = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("asap.auth.approval._utcnow", lambda: frozen)
    store = InMemoryApprovalStore()
    first = await create_device_authorization(store, _AGENT, ["read"], expires_in=30)
    assert first.user_code == _FIRST

    monkeypatch.setattr(
        "asap.auth.approval._utcnow",
        lambda: frozen + timedelta(seconds=60),
    )
    second = await create_device_authorization(store, _AGENT, ["read"], expires_in=30)

    assert second.user_code == _SECOND
    row = await store.get(_AGENT)
    assert row is not None
    assert row.status == "pending"
    assert row.user_code == _SECOND
    assert row.approved_by is None


@pytest.mark.asyncio
async def test_approved_device_authorization_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved device row is replaced instead of returned as the live challenge."""
    _codes(monkeypatch)
    store = InMemoryApprovalStore()
    await create_device_authorization(store, _AGENT, ["read"])
    await store.approve(_AGENT, "user-1")

    second = await create_device_authorization(store, _AGENT, ["read"])

    assert second.user_code == _SECOND
    row = await store.get(_AGENT)
    assert row is not None
    assert row.status == "pending"
    assert row.user_code == _SECOND
    assert row.approved_by is None


@pytest.mark.asyncio
async def test_denied_device_authorization_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied device row does not keep its old user code on the next request."""
    _codes(monkeypatch)
    store = InMemoryApprovalStore()
    await create_device_authorization(store, _AGENT, ["read"])
    await store.deny(_AGENT, "no trust")

    second = await create_device_authorization(store, _AGENT, ["read"])

    assert second.user_code == _SECOND
    row = await store.get(_AGENT)
    assert row is not None
    assert row.status == "pending"
    assert row.deny_reason is None
    assert row.user_code == _SECOND


@pytest.mark.asyncio
async def test_approved_ciba_request_is_replaced() -> None:
    """An approved CIBA row is replaced by a new pending request."""
    store = InMemoryApprovalStore()
    await create_ciba_approval(store, _AGENT, ["read"], binding_message="first prompt")
    await store.approve(_AGENT, "user-1")

    fresh = await create_ciba_approval(store, _AGENT, ["read"], binding_message="second prompt")

    assert fresh.binding_message == "second prompt"
    row = await store.get(_AGENT)
    assert row is not None
    assert row.status == "pending"
    assert row.approved_by is None
    assert row.binding_message == "second prompt"
