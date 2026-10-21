"""Legacy delegation databases predate the delegate_urn column.

CREATE TABLE IF NOT EXISTS does not add a column to an existing table, so
cascade revoke and token detail must run the one-time ALTER.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from asap.economics.delegation_storage import SQLiteDelegationStorage

_LEGACY_TOKEN = "legacy-token"
_LEGACY_DELEGATOR = "urn:asap:agent:legacy-root"
_LEGACY_CREATED_AT = "2024-01-01T00:00:00+00:00"


def _create_pre_delegate_urn_db(path: Path) -> None:
    """Write the issued_delegations shape from before delegate_urn existed."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE revocations (
                id TEXT PRIMARY KEY,
                revoked_at TEXT NOT NULL,
                reason TEXT
            );
            CREATE TABLE issued_delegations (
                id TEXT PRIMARY KEY,
                delegator_urn TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO issued_delegations (id, delegator_urn, created_at) VALUES (?, ?, ?)",
            (_LEGACY_TOKEN, _LEGACY_DELEGATOR, _LEGACY_CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()


def _issued_column(path: Path, name: str) -> tuple[str, int, str | None] | None:
    """Return ``(type, notnull, default)`` for ``name``, or None when it is absent."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("PRAGMA table_info(issued_delegations)").fetchall()
    finally:
        connection.close()
    for row in rows:
        if str(row[1]) == name:
            default = None if row[4] is None else str(row[4])
            return (str(row[2]), int(row[3]), default)
    return None


def _legacy_delegate_and_created_at(path: Path) -> tuple[object, object]:
    """Read the stored delegate and created_at for the pre-upgrade row."""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT delegate_urn, created_at FROM issued_delegations WHERE id = ?",
            (_LEGACY_TOKEN,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError(f"issued_delegations has no row for {_LEGACY_TOKEN!r}")
    return (row[0], row[1])


@pytest.mark.asyncio
async def test_legacy_delegation_db_gains_delegate_urn_and_stays_readable(
    tmp_path: Path,
) -> None:
    """Opening a pre-column database must ALTER once, then serve cascade and reads."""
    db_path = tmp_path / "legacy-delegations.db"
    _create_pre_delegate_urn_db(db_path)
    assert _issued_column(db_path, "delegate_urn") is None

    storage = SQLiteDelegationStorage(db_path=db_path)
    detail = await storage.get_token_detail(_LEGACY_TOKEN)
    assert detail is not None
    assert detail.delegator_urn == _LEGACY_DELEGATOR
    assert detail.delegate_urn is None
    assert detail.created_at == datetime.fromisoformat(_LEGACY_CREATED_AT)
    assert detail.is_revoked is False
    assert _issued_column(db_path, "delegate_urn") == ("TEXT", 0, None)
    delegate_urn, created_at = _legacy_delegate_and_created_at(db_path)
    assert delegate_urn is None
    assert created_at == _LEGACY_CREATED_AT

    await storage.register_issued(
        "child-token",
        _LEGACY_DELEGATOR,
        delegate_urn="urn:asap:agent:legacy-delegate",
    )
    await storage.revoke_cascade(_LEGACY_TOKEN, reason="schema-upgrade")

    assert await storage.is_revoked(_LEGACY_TOKEN) is True
    assert await storage.get_delegate("child-token") == "urn:asap:agent:legacy-delegate"
    assert _issued_column(db_path, "delegate_urn") == ("TEXT", 0, None)

    reopened = SQLiteDelegationStorage(db_path=db_path)
    reopened_detail = await reopened.get_token_detail(_LEGACY_TOKEN)
    assert reopened_detail is not None
    assert reopened_detail.delegate_urn is None
    assert reopened_detail.created_at == datetime.fromisoformat(_LEGACY_CREATED_AT)
    assert reopened_detail.is_revoked is True
