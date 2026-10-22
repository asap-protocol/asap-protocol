"""Approval requests close at the exact TTL deadline.

``_refresh_expired`` treats ``now >= created_at + expires_in`` as expired.
Existing coverage jumps 60 seconds past a 30 second TTL, so swapping ``>=``
for ``>`` would still pass. These cases pin the deadline instant and the
microsecond before it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from asap.auth.approval import (
    InMemoryApprovalStore,
    check_approval_status,
    create_device_authorization,
)

_ISSUED_AT = datetime(2022, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_EXPIRES_IN_SECONDS = 30


def _install_clock(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> dict[str, datetime]:
    """Freeze ``asap.auth.approval._utcnow`` on a mutable instant."""
    clock = {"instant": instant}

    def _now() -> datetime:
        return clock["instant"]

    monkeypatch.setattr("asap.auth.approval._utcnow", _now)
    return clock


@pytest.mark.asyncio
async def test_approve_one_microsecond_before_deadline_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending request is still approvable one microsecond before ``expires_in``."""
    store = InMemoryApprovalStore()
    clock = _install_clock(monkeypatch, _ISSUED_AT)
    await create_device_authorization(
        store,
        "edge-open",
        ["read"],
        expires_in=_EXPIRES_IN_SECONDS,
    )
    deadline = _ISSUED_AT + timedelta(seconds=_EXPIRES_IN_SECONDS)
    clock["instant"] = deadline - timedelta(microseconds=1)

    assert await check_approval_status(store, "edge-open") == "pending"
    await store.approve("edge-open", "user-1")
    assert await check_approval_status(store, "edge-open") == "approved"


@pytest.mark.asyncio
async def test_approve_at_exact_deadline_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """``approve`` rejects and persists ``expired`` when now equals the deadline."""
    store = InMemoryApprovalStore()
    clock = _install_clock(monkeypatch, _ISSUED_AT)
    await create_device_authorization(
        store,
        "edge-approve",
        ["read"],
        expires_in=_EXPIRES_IN_SECONDS,
    )
    clock["instant"] = _ISSUED_AT + timedelta(seconds=_EXPIRES_IN_SECONDS)

    with pytest.raises(ValueError, match="Approval request expired"):
        await store.approve("edge-approve", "user-1")

    # get() runs _refresh_expired again, so it would report expired even if
    # approve raised without storing the row.
    stored = store._by_agent["edge-approve"]
    assert stored.status == "expired"
    assert await check_approval_status(store, "edge-approve") == "expired"


@pytest.mark.asyncio
async def test_deny_at_exact_deadline_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """``deny`` uses the same deadline fence and does not record a denial."""
    store = InMemoryApprovalStore()
    clock = _install_clock(monkeypatch, _ISSUED_AT)
    await create_device_authorization(
        store,
        "edge-deny",
        ["read"],
        expires_in=_EXPIRES_IN_SECONDS,
    )
    clock["instant"] = _ISSUED_AT + timedelta(seconds=_EXPIRES_IN_SECONDS)

    with pytest.raises(ValueError, match="Approval request expired"):
        await store.deny("edge-deny", "too late")

    # Same as approve: read the stored row, not get(), which refreshes again.
    stored = store._by_agent["edge-deny"]
    assert stored.status == "expired"
    assert stored.deny_reason is None
