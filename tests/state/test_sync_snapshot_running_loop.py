"""Sync snapshot calls from a running loop must use the shared executor.

``asyncio.run`` raises when a loop is already running. FastAPI handlers that
still call the sync store depend on the thread-pool bridge for the round trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.models.entities import StateSnapshot
from asap.state.stores.sqlite import SQLiteSnapshotStore

_TASK_ID = "task_01HX5K4N0000000000000000RL"


def _snapshot(version: int) -> StateSnapshot:
    return StateSnapshot(
        id=f"snap_01HX5K7R0000000000000000{version:02d}",
        task_id=_TASK_ID,
        version=version,
        data={"step": version},
        checkpoint=False,
        created_at=datetime(2026, 6, 15, 12, 0, version, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_sync_snapshot_save_and_get_work_inside_a_running_loop(
    tmp_path: Path,
) -> None:
    """Two sync writes from an async test must both persist and read back."""
    store = SQLiteSnapshotStore(db_path=tmp_path / "snapshots.db")
    store.save(_snapshot(1))
    store.save(_snapshot(2))

    latest = store.get(_TASK_ID)
    versions = store.list_versions(_TASK_ID)

    assert latest is not None
    assert latest.version == 2
    assert latest.data == {"step": 2}
    assert versions == [1, 2]
