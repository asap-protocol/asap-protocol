"""Tests for ASAP snapshot store."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from asap.models.entities import StateSnapshot
from asap.models.types import TaskID
from asap.state.snapshot import (
    AsyncSnapshotStore,
    InMemorySnapshotStore,
    SnapshotStore,
)
from asap.state.stores.memory import AsyncInMemorySnapshotStore
from asap.state.stores.sqlite import SQLiteAsyncSnapshotStore


@pytest.fixture
def snapshot_store() -> InMemorySnapshotStore:
    """Create a fresh InMemorySnapshotStore for each test."""
    return InMemorySnapshotStore()


@pytest.fixture
def sample_snapshot() -> StateSnapshot:
    """Create a sample snapshot for testing."""
    now = datetime.now(timezone.utc)
    return StateSnapshot(
        id="snap_01HX5K7R000000000000000000",
        task_id="task_01HX5K4N000000000000000000",
        version=1,
        data={"status": "working", "progress": 50},
        checkpoint=False,
        created_at=now,
    )


class TestAsyncSnapshotStoreInterface:
    """Test the AsyncSnapshotStore interface contract."""

    def test_async_snapshot_store_is_runtime_checkable(self) -> None:
        """AsyncSnapshotStore supports isinstance checks."""
        from typing import runtime_checkable

        assert runtime_checkable(AsyncSnapshotStore)

    def test_async_protocol_methods_defined(self) -> None:
        """Protocol exposes the expected async method names."""
        for name in ("save", "get", "list_versions", "delete"):
            assert hasattr(AsyncSnapshotStore, name), name

    @pytest.mark.asyncio
    async def test_async_impl_isinstance_protocol(self) -> None:
        """A class with async CRUD methods satisfies AsyncSnapshotStore."""

        class _AsyncSnapshotStoreStub:
            async def save(self, snapshot: StateSnapshot) -> None:
                _ = snapshot

            async def get(
                self, task_id: TaskID, version: int | None = None
            ) -> StateSnapshot | None:
                _ = (task_id, version)
                return None

            async def list_versions(self, task_id: TaskID) -> list[int]:
                _ = task_id
                return []

            async def delete(self, task_id: TaskID, version: int | None = None) -> bool:
                _ = (task_id, version)
                return False

        stub = _AsyncSnapshotStoreStub()
        assert isinstance(stub, AsyncSnapshotStore)

    def test_sync_memory_store_exposes_sync_callables_only(self) -> None:
        """InMemorySnapshotStore is sync; Protocol isinstance cannot enforce async def."""

        store = InMemorySnapshotStore()
        for name in ("save", "get", "list_versions", "delete"):
            assert not inspect.iscoroutinefunction(getattr(store, name))

    def test_async_stub_methods_are_coroutines(self) -> None:
        """True async implementations use coroutine functions for CRUD."""

        class _AsyncSnapshotStoreStub:
            async def save(self, snapshot: StateSnapshot) -> None:
                _ = snapshot

            async def get(
                self, task_id: TaskID, version: int | None = None
            ) -> StateSnapshot | None:
                _ = (task_id, version)
                return None

            async def list_versions(self, task_id: TaskID) -> list[int]:
                _ = task_id
                return []

            async def delete(self, task_id: TaskID, version: int | None = None) -> bool:
                _ = (task_id, version)
                return False

        stub = _AsyncSnapshotStoreStub()
        for name in ("save", "get", "list_versions", "delete"):
            assert inspect.iscoroutinefunction(getattr(stub, name))


class TestSnapshotStoreInterface:
    """Test the SnapshotStore interface contract."""

    def test_snapshot_store_is_protocol(self) -> None:
        """Test that SnapshotStore is a Protocol."""
        from typing import runtime_checkable

        assert hasattr(SnapshotStore, "__protocol_attrs__") or runtime_checkable(SnapshotStore)

    def test_protocol_methods_defined(self) -> None:
        """Test that protocol methods are properly defined."""
        assert hasattr(SnapshotStore, "save")
        assert hasattr(SnapshotStore, "get")
        assert hasattr(SnapshotStore, "list_versions")
        assert hasattr(SnapshotStore, "delete")

        store = InMemorySnapshotStore()
        assert callable(store.save)
        assert callable(store.get)
        assert callable(store.list_versions)
        assert callable(store.delete)

    def test_in_memory_store_implements_protocol(self) -> None:
        """Test that InMemorySnapshotStore implements SnapshotStore protocol."""
        store = InMemorySnapshotStore()
        assert isinstance(store, SnapshotStore)

    def test_subclassing_snapshot_store_emits_deprecation_warning(self) -> None:
        """SnapshotStore is deprecated for new inheritance (PEP 702)."""

        with pytest.warns(DeprecationWarning, match="AsyncSnapshotStore"):

            class _LegacySubclass(SnapshotStore):
                def save(self, snapshot: StateSnapshot) -> None:
                    _ = snapshot

                def get(self, task_id: TaskID, version: int | None = None) -> StateSnapshot | None:
                    _ = (task_id, version)
                    return None

                def list_versions(self, task_id: TaskID) -> list[int]:
                    _ = task_id
                    return []

                def delete(self, task_id: TaskID, version: int | None = None) -> bool:
                    _ = (task_id, version)
                    return False


class TestAsyncInMemorySnapshotStore:
    @pytest.mark.asyncio
    async def test_save_get_roundtrip(self, sample_snapshot: StateSnapshot) -> None:
        store = AsyncInMemorySnapshotStore()
        assert isinstance(store, AsyncSnapshotStore)
        await store.save(sample_snapshot)
        got = await store.get(sample_snapshot.task_id, sample_snapshot.version)
        assert got is not None
        assert got.id == sample_snapshot.id

    @pytest.mark.asyncio
    async def test_get_latest_and_list_versions(self, sample_snapshot: StateSnapshot) -> None:
        store = AsyncInMemorySnapshotStore()
        await store.save(sample_snapshot)
        v2 = StateSnapshot(
            id="snap_async_v2",
            task_id=sample_snapshot.task_id,
            version=2,
            data={"step": 2},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(v2)
        latest = await store.get(sample_snapshot.task_id, None)
        assert latest is not None and latest.version == 2
        assert await store.list_versions(sample_snapshot.task_id) == [1, 2]

    @pytest.mark.asyncio
    async def test_get_unknown_task_returns_none(self) -> None:
        store = AsyncInMemorySnapshotStore()
        assert await store.get("unknown_task", None) is None
        assert await store.list_versions("unknown_task") == []

    @pytest.mark.asyncio
    async def test_delete_specific_version_then_all(self, sample_snapshot: StateSnapshot) -> None:
        store = AsyncInMemorySnapshotStore()
        await store.save(sample_snapshot)
        v2 = StateSnapshot(
            id="snap_async_v2b",
            task_id=sample_snapshot.task_id,
            version=2,
            data={},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(v2)
        assert await store.delete(sample_snapshot.task_id, 1) is True
        assert await store.get(sample_snapshot.task_id, 1) is None
        assert await store.get(sample_snapshot.task_id, 2) is not None
        assert await store.delete(sample_snapshot.task_id, None) is True
        assert await store.list_versions(sample_snapshot.task_id) == []

    @pytest.mark.asyncio
    async def test_delete_unknown_returns_false(self) -> None:
        store = AsyncInMemorySnapshotStore()
        assert await store.delete("missing_task", None) is False
        assert await store.delete("missing_task", 1) is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent_version_returns_false(
        self, sample_snapshot: StateSnapshot
    ) -> None:
        store = AsyncInMemorySnapshotStore()
        await store.save(sample_snapshot)
        assert await store.delete(sample_snapshot.task_id, 99) is False

    @pytest.mark.asyncio
    async def test_delete_latest_repoints_to_previous_version(
        self, sample_snapshot: StateSnapshot
    ) -> None:
        store = AsyncInMemorySnapshotStore()
        await store.save(sample_snapshot)
        v2 = StateSnapshot(
            id="snap_async_v2c",
            task_id=sample_snapshot.task_id,
            version=2,
            data={},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(v2)
        assert await store.delete(sample_snapshot.task_id, 2) is True
        latest = await store.get(sample_snapshot.task_id, None)
        assert latest is not None and latest.version == 1


class TestSQLiteAsyncSnapshotStore:
    """Integration tests for SQLite async snapshot persistence (aiosqlite + WAL)."""

    @pytest.mark.asyncio
    async def test_save_get_roundtrip(self, tmp_path: Path, sample_snapshot: StateSnapshot) -> None:
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "test.db")
        assert isinstance(store, AsyncSnapshotStore)
        await store.save(sample_snapshot)
        got = await store.get(sample_snapshot.task_id, sample_snapshot.version)
        assert got is not None
        assert got.id == sample_snapshot.id
        assert got.data == sample_snapshot.data

    @pytest.mark.asyncio
    async def test_get_latest_and_list_versions(
        self, tmp_path: Path, sample_snapshot: StateSnapshot
    ) -> None:
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "versions.db")
        await store.save(sample_snapshot)
        v2 = StateSnapshot(
            id="snap_sqlite_v2_01HX5K7R000000000000000001",
            task_id=sample_snapshot.task_id,
            version=2,
            data={"step": 2},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(v2)
        latest = await store.get(sample_snapshot.task_id, None)
        assert latest is not None and latest.version == 2
        assert await store.list_versions(sample_snapshot.task_id) == [1, 2]

    @pytest.mark.asyncio
    async def test_delete_specific_version_then_all(
        self, tmp_path: Path, sample_snapshot: StateSnapshot
    ) -> None:
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "delete.db")
        await store.save(sample_snapshot)
        v2 = StateSnapshot(
            id="snap_sqlite_v2b_01HX5K7R000000000000000002",
            task_id=sample_snapshot.task_id,
            version=2,
            data={},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(v2)
        assert await store.delete(sample_snapshot.task_id, 1) is True
        assert await store.get(sample_snapshot.task_id, 1) is None
        assert await store.delete(sample_snapshot.task_id, None) is True
        assert await store.list_versions(sample_snapshot.task_id) == []

    @pytest.mark.asyncio
    async def test_concurrent_saves_do_not_raise(self, tmp_path: Path) -> None:
        """Multiple async saves to the same DB should not produce 'database is locked'."""
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "concurrent.db")
        task_id: TaskID = "task_01HX5K4N000000000000000099"
        now = datetime.now(timezone.utc)
        snapshots = [
            StateSnapshot(
                id=f"snap_concurrent_01HX5K7R0000000000000000{i:02d}",
                task_id=task_id,
                version=i,
                data={"i": i},
                checkpoint=False,
                created_at=now,
            )
            for i in range(1, 11)
        ]
        await asyncio.gather(*(store.save(s) for s in snapshots))
        assert await store.list_versions(task_id) == list(range(1, 11))

    @pytest.mark.asyncio
    async def test_save_same_version_replaces_row(
        self, tmp_path: Path, sample_snapshot: StateSnapshot
    ) -> None:
        """Primary key (task_id, version) replaces the prior row instead of duplicating it."""
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "replace.db")
        await store.save(sample_snapshot)
        replacement_created = sample_snapshot.created_at + timedelta(seconds=60)
        replacement = StateSnapshot(
            id="snap_01HX5K7R000000000000000099",
            task_id=sample_snapshot.task_id,
            version=sample_snapshot.version,
            data={"status": "completed", "progress": 100},
            checkpoint=True,
            created_at=replacement_created,
        )
        await store.save(replacement)

        got = await store.get(sample_snapshot.task_id, sample_snapshot.version)
        assert got is not None
        assert got.id == replacement.id
        assert got.data == {"status": "completed", "progress": 100}
        assert got.checkpoint is True
        assert got.created_at == replacement_created
        assert await store.list_versions(sample_snapshot.task_id) == [1]
        latest = await store.get(sample_snapshot.task_id)
        assert latest is not None and latest.id == replacement.id

    @pytest.mark.asyncio
    async def test_delete_highest_version_repoints_latest(
        self, tmp_path: Path, sample_snapshot: StateSnapshot
    ) -> None:
        """Deleting the highest version makes get() return the previous snapshot."""
        store = SQLiteAsyncSnapshotStore(db_path=tmp_path / "latest-after-delete.db")
        await store.save(sample_snapshot)
        newer = StateSnapshot(
            id="snap_sqlite_v2_01HX5K7R000000000000000001",
            task_id=sample_snapshot.task_id,
            version=2,
            data={"step": 2},
            checkpoint=False,
            created_at=sample_snapshot.created_at,
        )
        await store.save(newer)

        assert await store.delete(sample_snapshot.task_id, 2) is True
        latest = await store.get(sample_snapshot.task_id)
        assert latest is not None
        assert latest.version == 1
        assert latest.id == sample_snapshot.id
        assert await store.get(sample_snapshot.task_id, 2) is None
        assert await store.list_versions(sample_snapshot.task_id) == [1]


class TestInMemorySnapshotStore:
    """Test the InMemorySnapshotStore implementation."""

    def test_save_snapshot_stores_correctly(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test that save() stores snapshots correctly."""
        snapshot_store.save(sample_snapshot)

        # Verify the snapshot was stored
        retrieved = snapshot_store.get(sample_snapshot.task_id, sample_snapshot.version)
        assert retrieved is not None
        assert retrieved.id == sample_snapshot.id
        assert retrieved.task_id == sample_snapshot.task_id
        assert retrieved.version == sample_snapshot.version
        assert retrieved.data == sample_snapshot.data
        assert retrieved.checkpoint == sample_snapshot.checkpoint

    def test_get_latest_snapshot_returns_most_recent(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test that get(task_id) returns the latest snapshot."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        # Create multiple snapshots for the same task
        snapshot_v1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id,
            version=1,
            data={"step": 1},
            created_at=now,
        )

        snapshot_v2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id,
            version=2,
            data={"step": 2},
            created_at=now,
        )

        snapshot_v3 = StateSnapshot(
            id="snap_01HX5K7R000000000000000003",
            task_id=task_id,
            version=3,
            data={"step": 3},
            created_at=now,
        )

        # Save them (not necessarily in order)
        snapshot_store.save(snapshot_v1)
        snapshot_store.save(snapshot_v3)
        snapshot_store.save(snapshot_v2)

        # Get latest (should be v3)
        latest = snapshot_store.get(task_id)
        assert latest is not None
        assert latest.version == 3
        assert latest.data["step"] == 3

    def test_get_specific_version_returns_correct_snapshot(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test that get(task_id, version) returns the specific version."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        # Create snapshots with different versions
        snapshot_v1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id,
            version=1,
            data={"step": 1},
            created_at=now,
        )

        snapshot_v2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id,
            version=2,
            data={"step": 2},
            created_at=now,
        )

        snapshot_store.save(snapshot_v1)
        snapshot_store.save(snapshot_v2)

        # Get specific versions
        retrieved_v1 = snapshot_store.get(task_id, 1)
        assert retrieved_v1 is not None
        assert retrieved_v1.version == 1
        assert retrieved_v1.data["step"] == 1

        retrieved_v2 = snapshot_store.get(task_id, 2)
        assert retrieved_v2 is not None
        assert retrieved_v2.version == 2
        assert retrieved_v2.data["step"] == 2

    def test_list_versions_returns_all_versions_for_task(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test that list_versions(task_id) returns all versions."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        # Create snapshots with versions 1, 3, 2 (out of order)
        snapshots = [
            StateSnapshot(
                id=f"snap_01HX5K7R00000000000000000{i}",
                task_id=task_id,
                version=version,
                data={"step": version},
                created_at=now,
            )
            for version, i in [(1, 1), (3, 3), (2, 2)]
        ]

        for snapshot in snapshots:
            snapshot_store.save(snapshot)

        # List versions should be sorted
        versions = snapshot_store.list_versions(task_id)
        assert versions == [1, 2, 3]

    def test_version_auto_increment_not_handled_by_store(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test that the store doesn't auto-increment versions (that's handled by caller)."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        # Save snapshots with manually set versions
        snapshot_v5 = StateSnapshot(
            id="snap_01HX5K7R000000000000000005",
            task_id=task_id,
            version=5,
            data={"manual_version": True},
            created_at=now,
        )

        snapshot_store.save(snapshot_v5)

        # Store just accepts whatever version is provided
        retrieved = snapshot_store.get(task_id, 5)
        assert retrieved is not None
        assert retrieved.version == 5

    def test_get_nonexistent_task_returns_none(self, snapshot_store: InMemorySnapshotStore) -> None:
        """Test that get() returns None for non-existent task."""
        nonexistent_task_id = "task_nonexistent"
        result = snapshot_store.get(nonexistent_task_id)
        assert result is None

    def test_get_nonexistent_version_returns_none(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test that get() returns None for non-existent version."""
        snapshot_store.save(sample_snapshot)

        # Try to get a version that doesn't exist
        result = snapshot_store.get(sample_snapshot.task_id, 999)
        assert result is None

    def test_list_versions_empty_for_nonexistent_task(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test that list_versions() returns empty list for non-existent task."""
        nonexistent_task_id = "task_nonexistent"
        versions = snapshot_store.list_versions(nonexistent_task_id)
        assert versions == []

    def test_multiple_tasks_isolation(self, snapshot_store: InMemorySnapshotStore) -> None:
        """Test that snapshots from different tasks are properly isolated."""
        now = datetime.now(timezone.utc)

        task_id_1 = "task_01HX5K4N000000000000000001"
        task_id_2 = "task_01HX5K4N000000000000000002"

        # Create snapshots for task_id_1
        snapshot_1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id_1,
            version=1,
            data={"task": 1},
            created_at=now,
        )

        # Create snapshots for task_id_2
        snapshot_2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id_2,
            version=1,
            data={"task": 2},
            created_at=now,
        )

        snapshot_store.save(snapshot_1)
        snapshot_store.save(snapshot_2)

        # Verify isolation
        retrieved_1 = snapshot_store.get(task_id_1)
        assert retrieved_1 is not None
        assert retrieved_1.data["task"] == 1

        retrieved_2 = snapshot_store.get(task_id_2)
        assert retrieved_2 is not None
        assert retrieved_2.data["task"] == 2

        # Versions should be separate
        versions_1 = snapshot_store.list_versions(task_id_1)
        versions_2 = snapshot_store.list_versions(task_id_2)
        assert versions_1 == [1]
        assert versions_2 == [1]

    def test_get_latest_when_no_snapshots_exist(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test get() returns None when no snapshots exist for task."""
        # Save snapshot for one task
        snapshot_store.save(sample_snapshot)

        # Try to get latest for different task
        nonexistent_task_id = "task_nonexistent"
        result = snapshot_store.get(nonexistent_task_id)
        assert result is None

    def test_get_latest_when_task_has_no_versions(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test get() returns None when task exists but has no versions."""
        # This edge case shouldn't happen in practice, but let's test it
        task_id = "task_empty"

        # Manually create empty dict for task (simulating corrupted state)
        snapshot_store._snapshots[task_id] = {}
        # But don't set latest version

        result = snapshot_store.get(task_id)
        assert result is None

    def test_list_versions_returns_empty_for_empty_task_dict(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test list_versions() returns empty list when task dict exists but is empty."""
        task_id = "task_empty"
        snapshot_store._snapshots[task_id] = {}  # Empty dict

        versions = snapshot_store.list_versions(task_id)
        assert versions == []

    def test_delete_specific_version(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test delete() removes specific version."""
        task_id = sample_snapshot.task_id
        now = datetime.now(timezone.utc)

        # Create multiple versions
        snapshot_v1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id,
            version=1,
            data={"step": 1},
            created_at=now,
        )
        snapshot_v2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id,
            version=2,
            data={"step": 2},
            created_at=now,
        )

        snapshot_store.save(snapshot_v1)
        snapshot_store.save(snapshot_v2)

        # Delete version 1
        result = snapshot_store.delete(task_id, version=1)
        assert result is True

        # Version 1 should be gone
        assert snapshot_store.get(task_id, version=1) is None
        # Version 2 should still exist
        assert snapshot_store.get(task_id, version=2) is not None
        # Latest should now be version 2
        assert snapshot_store.get(task_id).version == 2

    def test_delete_all_versions(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test delete() removes all versions when version is None."""
        task_id = sample_snapshot.task_id
        now = datetime.now(timezone.utc)

        # Create multiple versions
        snapshot_v1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id,
            version=1,
            data={"step": 1},
            created_at=now,
        )
        snapshot_v2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id,
            version=2,
            data={"step": 2},
            created_at=now,
        )

        snapshot_store.save(snapshot_v1)
        snapshot_store.save(snapshot_v2)

        # Delete all versions
        result = snapshot_store.delete(task_id)
        assert result is True

        # All versions should be gone
        assert snapshot_store.get(task_id) is None
        assert snapshot_store.get(task_id, version=1) is None
        assert snapshot_store.get(task_id, version=2) is None
        assert snapshot_store.list_versions(task_id) == []

    def test_delete_nonexistent_task_returns_false(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test delete() returns False for non-existent task."""
        result = snapshot_store.delete("task_nonexistent")
        assert result is False

    def test_delete_nonexistent_version_returns_false(
        self, snapshot_store: InMemorySnapshotStore, sample_snapshot: StateSnapshot
    ) -> None:
        """Test delete() returns False for non-existent version."""
        snapshot_store.save(sample_snapshot)

        result = snapshot_store.delete(sample_snapshot.task_id, version=999)
        assert result is False

        # Original snapshot should still exist
        assert snapshot_store.get(sample_snapshot.task_id) is not None

    def test_delete_latest_version_updates_latest(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test delete() of latest version updates _latest_versions to next max."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        for v in [1, 2, 3]:
            snapshot = StateSnapshot(
                id=f"snap_v{v}",
                task_id=task_id,
                version=v,
                data={"v": v},
                created_at=now,
            )
            snapshot_store.save(snapshot)

        assert snapshot_store._latest_versions[task_id] == 3
        assert snapshot_store.get(task_id).version == 3

        result = snapshot_store.delete(task_id, version=3)
        assert result is True

        assert snapshot_store._latest_versions[task_id] == 2
        assert snapshot_store.get(task_id).version == 2

        assert snapshot_store.get(task_id, version=3) is None
        assert snapshot_store.get(task_id, version=1) is not None
        assert snapshot_store.get(task_id, version=2) is not None

    def test_delete_last_version_cleans_up_task(
        self, snapshot_store: InMemorySnapshotStore
    ) -> None:
        """Test delete() of the last remaining version cleans up task entirely."""
        task_id = "task_01HX5K4N000000000000000000"
        now = datetime.now(timezone.utc)

        snapshot = StateSnapshot(
            id="snap_only",
            task_id=task_id,
            version=1,
            data={"only": True},
            created_at=now,
        )
        snapshot_store.save(snapshot)

        assert task_id in snapshot_store._snapshots
        assert task_id in snapshot_store._latest_versions

        result = snapshot_store.delete(task_id, version=1)
        assert result is True

        assert task_id not in snapshot_store._snapshots
        assert task_id not in snapshot_store._latest_versions
        assert snapshot_store.get(task_id) is None
        assert snapshot_store.list_versions(task_id) == []

    def test_delete_preserves_other_tasks(self, snapshot_store: InMemorySnapshotStore) -> None:
        """Test delete() only affects the specified task."""
        now = datetime.now(timezone.utc)

        task_id_1 = "task_01HX5K4N000000000000000001"
        task_id_2 = "task_01HX5K4N000000000000000002"

        snapshot_1 = StateSnapshot(
            id="snap_01HX5K7R000000000000000001",
            task_id=task_id_1,
            version=1,
            data={"task": 1},
            created_at=now,
        )
        snapshot_2 = StateSnapshot(
            id="snap_01HX5K7R000000000000000002",
            task_id=task_id_2,
            version=1,
            data={"task": 2},
            created_at=now,
        )

        snapshot_store.save(snapshot_1)
        snapshot_store.save(snapshot_2)

        # Delete task_id_1
        snapshot_store.delete(task_id_1)

        # task_id_1 removed
        assert snapshot_store.get(task_id_1) is None
        # task_id_2 unchanged
        assert snapshot_store.get(task_id_2) is not None
        assert snapshot_store.get(task_id_2).data["task"] == 2


class TestSnapshotStoreThreadSafety:
    """Tests for thread safety of InMemorySnapshotStore."""

    def test_store_has_lock(self) -> None:
        """Test that InMemorySnapshotStore has internal lock for thread safety."""
        store = InMemorySnapshotStore()
        assert hasattr(store, "_lock")

    def test_concurrent_saves_to_same_task(self) -> None:
        """Test concurrent saves to the same task don't cause data corruption."""
        import concurrent.futures

        store = InMemorySnapshotStore()
        task_id = "task_concurrent_save"
        num_threads = 10
        versions_per_thread = 10
        errors: list[Exception] = []

        def save_snapshots(thread_id: int) -> None:
            try:
                for i in range(versions_per_thread):
                    version = thread_id * versions_per_thread + i + 1
                    snapshot = StateSnapshot(
                        id=f"snap_{thread_id}_{i}",
                        task_id=task_id,
                        version=version,
                        data={"thread": thread_id, "iteration": i},
                        created_at=datetime.now(timezone.utc),
                    )
                    store.save(snapshot)
            except Exception as e:
                errors.append(e)

        # Run concurrent saves
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(save_snapshots, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        # No errors should have occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # All versions should be saved
        versions = store.list_versions(task_id)
        assert len(versions) == num_threads * versions_per_thread

    def test_concurrent_saves_to_different_tasks(self) -> None:
        """Test concurrent saves to different tasks work correctly."""
        import concurrent.futures

        store = InMemorySnapshotStore()
        num_tasks = 20
        versions_per_task = 5
        errors: list[Exception] = []

        def save_task_snapshots(task_num: int) -> None:
            try:
                task_id = f"task_{task_num}"
                for version in range(1, versions_per_task + 1):
                    snapshot = StateSnapshot(
                        id=f"snap_{task_num}_{version}",
                        task_id=task_id,
                        version=version,
                        data={"task_num": task_num, "version": version},
                        created_at=datetime.now(timezone.utc),
                    )
                    store.save(snapshot)
            except Exception as e:
                errors.append(e)

        # Run concurrent saves
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_tasks) as executor:
            futures = [executor.submit(save_task_snapshots, i) for i in range(num_tasks)]
            concurrent.futures.wait(futures)

        # No errors should have occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Each task should have all its versions
        for task_num in range(num_tasks):
            task_id = f"task_{task_num}"
            versions = store.list_versions(task_id)
            assert len(versions) == versions_per_task
            assert versions == list(range(1, versions_per_task + 1))

    def test_concurrent_read_write(self) -> None:
        """Test concurrent reads and writes don't cause issues."""
        import concurrent.futures

        store = InMemorySnapshotStore()
        task_id = "task_read_write"
        num_writers = 5
        num_readers = 10
        writes_per_writer = 20
        reads_per_reader = 50
        errors: list[Exception] = []

        # Pre-populate some data
        for version in range(1, 6):
            snapshot = StateSnapshot(
                id=f"snap_init_{version}",
                task_id=task_id,
                version=version,
                data={"initial": True, "version": version},
                created_at=datetime.now(timezone.utc),
            )
            store.save(snapshot)

        def writer(writer_id: int) -> None:
            try:
                for i in range(writes_per_writer):
                    version = 100 + writer_id * writes_per_writer + i
                    snapshot = StateSnapshot(
                        id=f"snap_writer_{writer_id}_{i}",
                        task_id=task_id,
                        version=version,
                        data={"writer": writer_id, "iteration": i},
                        created_at=datetime.now(timezone.utc),
                    )
                    store.save(snapshot)
            except Exception as e:
                errors.append(e)

        def reader(reader_id: int) -> None:
            try:
                for _ in range(reads_per_reader):
                    # Mix of operations
                    _ = store.get(task_id)
                    _ = store.list_versions(task_id)
                    _ = store.get(task_id, version=1)
            except Exception as e:
                errors.append(e)

        # Run concurrent reads and writes
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=num_writers + num_readers
        ) as executor:
            futures = []
            for i in range(num_writers):
                futures.append(executor.submit(writer, i))
            for i in range(num_readers):
                futures.append(executor.submit(reader, i))
            concurrent.futures.wait(futures)

        # No errors should have occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

    def test_concurrent_delete_operations(self) -> None:
        """Test concurrent delete operations are safe."""
        import concurrent.futures

        store = InMemorySnapshotStore()
        num_tasks = 10
        versions_per_task = 5

        # Pre-populate data
        for task_num in range(num_tasks):
            task_id = f"task_{task_num}"
            for version in range(1, versions_per_task + 1):
                snapshot = StateSnapshot(
                    id=f"snap_{task_num}_{version}",
                    task_id=task_id,
                    version=version,
                    data={"task_num": task_num, "version": version},
                    created_at=datetime.now(timezone.utc),
                )
                store.save(snapshot)

        errors: list[Exception] = []

        def delete_task(task_num: int) -> None:
            try:
                task_id = f"task_{task_num}"
                # Delete each version individually
                for version in range(1, versions_per_task + 1):
                    store.delete(task_id, version=version)
            except Exception as e:
                errors.append(e)

        # Run concurrent deletes
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_tasks) as executor:
            futures = [executor.submit(delete_task, i) for i in range(num_tasks)]
            concurrent.futures.wait(futures)

        # No errors should have occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # All tasks should be empty
        for task_num in range(num_tasks):
            task_id = f"task_{task_num}"
            assert store.list_versions(task_id) == []

    def test_concurrent_mixed_operations(self) -> None:
        """Test all operations running concurrently are thread-safe."""
        import concurrent.futures
        import random

        store = InMemorySnapshotStore()
        num_threads = 20
        operations_per_thread = 50
        errors: list[Exception] = []

        def mixed_operations(thread_id: int) -> None:
            try:
                task_id = f"task_{thread_id % 5}"  # Use 5 shared tasks
                for i in range(operations_per_thread):
                    op = random.choice(["save", "get", "get_version", "list", "delete"])

                    if op == "save":
                        version = thread_id * operations_per_thread + i + 1
                        snapshot = StateSnapshot(
                            id=f"snap_{thread_id}_{i}",
                            task_id=task_id,
                            version=version,
                            data={"thread": thread_id, "iteration": i},
                            created_at=datetime.now(timezone.utc),
                        )
                        store.save(snapshot)
                    elif op == "get":
                        _ = store.get(task_id)
                    elif op == "get_version":
                        _ = store.get(task_id, version=random.randint(1, 100))
                    elif op == "list":
                        _ = store.list_versions(task_id)
                    elif op == "delete":
                        store.delete(task_id, version=random.randint(1, 100))
            except Exception as e:
                errors.append(e)

        # Run concurrent mixed operations
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(mixed_operations, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        # No errors should have occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"
