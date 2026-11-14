"""Snapshot backend names are exact after ``strip().lower()``.

``memory`` and ``sqlite`` are the only backends. ``redis`` is already rejected
and is not a neighbor. Padded ``MeMoRy`` and ``SQLITE`` stay accepted in
``tests/state/test_storage_factory.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asap.state.snapshot import create_async_snapshot_store
from asap.state.stores import create_snapshot_store

_NEIGHBORS: tuple[str, ...] = ("memoryextra", "memor", "sqliteextra", "sqlit")


def test_async_snapshot_backend_is_exact(tmp_path: Path) -> None:
    """A neighbor does not construct an async snapshot store."""
    for name in _NEIGHBORS:
        db_path = tmp_path / f"{name}.db"
        with pytest.raises(ValueError, match="Unknown async snapshot backend") as exc_info:
            create_async_snapshot_store(name, db_path=db_path)
        assert f"{name!r}" in str(exc_info.value)
        assert not db_path.exists()


def test_snapshot_store_backend_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A neighbor does not construct the env-selected snapshot store."""
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("ASAP_STORAGE_PATH", str(db_path))
    for name in _NEIGHBORS:
        monkeypatch.setenv("ASAP_STORAGE_BACKEND", name)
        with pytest.raises(ValueError, match="Unknown ASAP_STORAGE_BACKEND") as exc_info:
            create_snapshot_store()
        assert f"{name!r}" in str(exc_info.value)
        assert not db_path.exists()
