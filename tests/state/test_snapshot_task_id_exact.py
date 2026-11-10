"""Snapshot reads and deletes must use the full task id.

A prefix, case, or padding match would restore or erase another task's state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.models.entities import StateSnapshot
from asap.state.snapshot import InMemorySnapshotStore
from asap.state.stores.memory import AsyncInMemorySnapshotStore
from asap.state.stores.sqlite import SQLiteAsyncSnapshotStore

_EXACT_TASK = "task_01"
_SHORTER_TASK = "task_0"
_CREATED = datetime(2026, 11, 8, tzinfo=timezone.utc)
_ROWS: tuple[tuple[str, int, str], ...] = (
    ("task_010", 9, "longer"),
    (_SHORTER_TASK, 3, "shorter"),
    ("Task_01", 7, "case"),
    ("task_01 ", 8, "trail"),
    (" task_01", 6, "lead"),
    (_EXACT_TASK, 1, "exact"),
)


def _snapshots() -> list[StateSnapshot]:
    """Neighbor rows first so a first-match scan does not land on the exact id."""
    return [
        StateSnapshot(
            id=f"snap_{marker}",
            task_id=task_id,
            version=version,
            data={"marker": marker},
            created_at=_CREATED,
        )
        for task_id, version, marker in _ROWS
    ]


def _version_for(task_id: str, marker: str) -> int:
    """Return the version stored for one task id and marker."""
    for row_id, version, row_marker in _ROWS:
        if row_id == task_id and row_marker == marker:
            return version
    raise AssertionError(f"missing snapshot row for {task_id!r} marker {marker!r}")


def _assert_marker(snapshot: StateSnapshot | None, task_id: str, marker: str) -> None:
    """Require the snapshot row that belongs to this exact task id."""
    assert snapshot is not None
    assert snapshot.task_id == task_id
    assert snapshot.data["marker"] == marker
    assert snapshot.version == _version_for(task_id, marker)


def test_memory_snapshot_task_id_match_is_exact() -> None:
    """Sync in-memory lookup and delete stay on the exact task id."""
    store = InMemorySnapshotStore()
    for snapshot in _snapshots():
        store.save(snapshot)
    for task_id, _version, marker in _ROWS:
        _assert_marker(store.get(task_id), task_id, marker)
    assert store.list_versions(_EXACT_TASK) == [1]
    for neighbor in ("Task_01", "task_01 ", " task_01"):
        assert store.delete(neighbor) is True
        _assert_marker(store.get(_EXACT_TASK), _EXACT_TASK, "exact")
        assert store.get(neighbor) is None
    assert store.delete(_SHORTER_TASK) is True
    _assert_marker(store.get(_EXACT_TASK), _EXACT_TASK, "exact")
    _assert_marker(store.get("task_010"), "task_010", "longer")
    assert store.get(_SHORTER_TASK) is None


async def _assert_async_isolation(
    store: AsyncInMemorySnapshotStore | SQLiteAsyncSnapshotStore,
) -> None:
    """Save neighbor rows and require exact task reads, lists, and deletes."""
    for snapshot in _snapshots():
        await store.save(snapshot)
    for task_id, _version, marker in _ROWS:
        _assert_marker(await store.get(task_id), task_id, marker)
    assert await store.list_versions(_EXACT_TASK) == [1]
    for neighbor in ("Task_01", "task_01 ", " task_01"):
        assert await store.delete(neighbor) is True
        _assert_marker(await store.get(_EXACT_TASK), _EXACT_TASK, "exact")
        assert await store.get(neighbor) is None
    assert await store.delete(_SHORTER_TASK) is True
    _assert_marker(await store.get(_EXACT_TASK), _EXACT_TASK, "exact")
    _assert_marker(await store.get("task_010"), "task_010", "longer")
    assert await store.get(_SHORTER_TASK) is None


@pytest.mark.asyncio
async def test_async_memory_snapshot_task_id_match_is_exact() -> None:
    """Async in-memory lookup and delete stay on the exact task id."""
    await _assert_async_isolation(AsyncInMemorySnapshotStore())


@pytest.mark.asyncio
async def test_sqlite_snapshot_task_id_match_is_exact(tmp_path: Path) -> None:
    """SQLite lookup and delete stay on the exact task id."""
    store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "snapshot-task-exact.db")
    await _assert_async_isolation(store)
