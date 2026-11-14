"""Audit export allow-lists are exact after ``strip().lower()``.

``--format`` is ``json``, ``csv``, or ``jsonl``. ``--store`` is ``sqlite`` or
``memory``. A neighboring token must not select a renderer or a backing store.
``xml`` and ``postgres`` are already rejected and are not neighbors of these
tokens.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from asap.cli import app

_FORMAT_ERROR = "--format must be 'json', 'csv', or 'jsonl'"
_STORE_ERROR = "--store must be 'sqlite' or 'memory'"
_FORMAT_NEIGHBORS: tuple[str, ...] = ("jsonextra", "jso", "csvv", "jsonlx")
_STORE_NEIGHBORS: tuple[str, ...] = ("sqliteextra", "sqlit", "memoryextra", "memor")


@pytest.fixture(autouse=True)
def _stable_typer_rich_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Rich from wrapping the BadParameter text on a narrow console."""
    import typer.rich_utils as rich_utils

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(rich_utils, "MAX_WIDTH", 200)


def _export(args: list[str]) -> Result:
    """Invoke ``asap audit export`` with ``args``."""
    return CliRunner().invoke(app, ["audit", "export", *args])


def test_audit_export_format_is_exact() -> None:
    """Superstrings and substrings of json, csv, and jsonl are rejected."""
    for fmt in _FORMAT_NEIGHBORS:
        result = _export(["--store", "memory", "--format", fmt])
        combined = result.stdout + result.stderr
        assert result.exit_code != 0
        assert _FORMAT_ERROR in combined


def test_audit_export_format_strips_and_lowercases() -> None:
    """Padding and case still select json and csv on an empty memory store."""
    json_result = _export(["--store", "memory", "--format", " JSON "])
    assert json_result.exit_code == 0, json_result.stdout + json_result.stderr
    assert json.loads(json_result.stdout.strip()) == []

    csv_result = _export(["--store", "memory", "--format", "CSV"])
    assert csv_result.exit_code == 0, csv_result.stdout + csv_result.stderr
    assert csv_result.stdout.splitlines()[0].startswith("id,")


def test_audit_export_store_is_exact(tmp_path: Path) -> None:
    """A neighbor of sqlite or memory does not open a database."""
    for store in _STORE_NEIGHBORS:
        db_path = tmp_path / f"{store}.db"
        result = _export(
            ["--store", store, "--db", str(db_path), "--format", "json"],
        )
        combined = result.stdout + result.stderr
        assert result.exit_code != 0
        assert _STORE_ERROR in combined
        assert not db_path.exists()


def test_audit_export_store_strips_and_lowercases(tmp_path: Path) -> None:
    """Padded case still selects sqlite and memory."""
    db_path = tmp_path / "audit.db"
    sqlite_result = _export(
        ["--store", " SQLITE ", "--db", str(db_path), "--format", "json"],
    )
    assert sqlite_result.exit_code == 0, sqlite_result.stdout + sqlite_result.stderr
    assert json.loads(sqlite_result.stdout.strip()) == []
    assert db_path.exists()

    memory_result = _export(["--store", " MeMoRy ", "--format", "json"])
    assert memory_result.exit_code == 0, memory_result.stdout + memory_result.stderr
    assert json.loads(memory_result.stdout.strip()) == []
