"""Latest-snapshot selection when versions arrive out of order.

The in-memory async store tracks a high-water version. SQLite selects
``ORDER BY version DESC``. A later write of an older version must not
become the snapshot returned for restore, and that older row must stay
readable by version. The SQLite version-3 row uses an earlier
``created_at`` so a ``created_at`` sort cannot tie with the version sort.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.models.entities import StateSnapshot
from asap.state.stores.memory import AsyncInMemorySnapshotStore
from asap.state.stores.sqlite import SQLiteAsyncSnapshotStore

_TASK_ID = "task_01HX5K4N000000000000000000"
_CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EARLIER_CREATED_AT = datetime(2025, 12, 1, tzinfo=timezone.utc)
_MIDDLE_CREATED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)
_LATER_CREATED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _snapshot(version: int, *, created_at: datetime = _CREATED_AT) -> StateSnapshot:
    """Build one snapshot whose payload records ``version``."""
    return StateSnapshot(
        id=f"snap_01HX5K7R00000000000000000{version}",
        task_id=_TASK_ID,
        version=version,
        data={"step": version},
        checkpoint=False,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_async_memory_latest_is_highest_version_not_last_write() -> None:
    """Saving version 2 after version 3 still restores version 3."""
    store = AsyncInMemorySnapshotStore()
    await store.save(_snapshot(1))
    await store.save(_snapshot(3))
    await store.save(_snapshot(2))

    latest = await store.get(_TASK_ID)
    assert latest is not None
    assert latest.version == 3
    assert latest.data["step"] == 3

    older = await store.get(_TASK_ID, 2)
    assert older is not None
    assert older.version == 2
    assert older.id == "snap_01HX5K7R000000000000000002"
    assert older.data["step"] == 2


@pytest.mark.asyncio
async def test_sqlite_latest_follows_version_not_insert_order(tmp_path: Path) -> None:
    """SQLite latest read uses the version column, not insert time.

    Version 3 is stored with an earlier ``created_at`` than the rows around
    it, so ``ORDER BY created_at DESC`` restores version 2 instead.
    """
    store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "version-order.db")
    await store.save(_snapshot(1, created_at=_MIDDLE_CREATED_AT))
    await store.save(_snapshot(3, created_at=_EARLIER_CREATED_AT))
    await store.save(_snapshot(2, created_at=_LATER_CREATED_AT))

    latest = await store.get(_TASK_ID)
    assert latest is not None
    assert latest.version == 3
    assert latest.data["step"] == 3
    assert latest.created_at == _EARLIER_CREATED_AT

    older = await store.get(_TASK_ID, 2)
    assert older is not None
    assert older.version == 2
    assert older.id == "snap_01HX5K7R000000000000000002"
    assert older.data["step"] == 2
