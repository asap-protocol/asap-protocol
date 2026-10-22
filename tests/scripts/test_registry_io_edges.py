"""IssueOps registry readers must not treat a partial document as a full list.

``load_registry`` of an object without ``agents`` is an empty registry.
``save_registry`` keeps a non-blank version and falls back to ``1.0`` when the
on-disk file is blank or unreadable. ``load_revoked`` without a ``revoked``
list returns the default document.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.registry_io import load_registry, load_revoked, save_registry  # noqa: E402

_AGENT: dict[str, str] = {"id": "urn:asap:agent:a:b", "name": "B"}


def test_load_registry_object_without_agents_returns_empty(tmp_path: Path) -> None:
    """A LiteRegistry envelope that lost its agents array is not a list of agents."""
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"version": "1.0", "updated_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    assert load_registry(str(path)) == []


def test_save_registry_strips_existing_version(tmp_path: Path) -> None:
    """Surrounding whitespace is not part of the stored schema version."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": "  2.1  ", "agents": []}), encoding="utf-8")
    save_registry(str(path), [_AGENT])
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "2.1"


@pytest.mark.parametrize("version", ["   ", 1, None])
def test_save_registry_defaults_blank_or_non_string_version(
    tmp_path: Path,
    version: object,
) -> None:
    """Blank, numeric, and null versions are not preserved."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": version, "agents": []}), encoding="utf-8")
    save_registry(str(path), [_AGENT])
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "1.0"


@pytest.mark.parametrize("payload", [b"{not json", b"\xff\xfe"])
def test_save_registry_defaults_version_when_existing_file_unreadable(
    tmp_path: Path,
    payload: bytes,
) -> None:
    """Corrupt JSON or non-UTF-8 bytes fall back to version 1.0 instead of raising."""
    path = tmp_path / "registry.json"
    path.write_bytes(payload)
    save_registry(str(path), [_AGENT])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == "1.0"
    assert saved["agents"] == [_AGENT]


@pytest.mark.parametrize("payload", ["{}", "[]"])
def test_load_revoked_without_revoked_list_returns_default(
    tmp_path: Path,
    payload: str,
) -> None:
    """An object or array without a revoked list is the empty revocation document."""
    path = tmp_path / "revoked_agents.json"
    path.write_text(payload, encoding="utf-8")
    assert load_revoked(str(path)) == {"revoked": [], "version": "1.0"}
