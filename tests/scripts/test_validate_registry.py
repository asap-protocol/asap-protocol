"""Tests for scripts/validate_registry.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_registry import validate_registry


REGISTRY_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "registry"


def test_shellclaw_agents_array_fixture_validates() -> None:
    """ShellClaw registry array fixture passes the CI registry validator."""
    path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-agents-array.json"
    assert validate_registry(path) == []


def test_lite_registry_with_hardware_fields_validates(tmp_path: Path) -> None:
    """LiteRegistry object form accepts v2.4 hardware and inference mirror fields."""
    entry_path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-entry.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "updated_at": "2026-05-24T00:00:00Z",
                "agents": [entry],
            }
        ),
        encoding="utf-8",
    )

    assert validate_registry(registry_path) == []


def test_agents_array_rejects_string_hardware_io(tmp_path: Path) -> None:
    """Array-form registry rejects hardware_io when it is not a list."""
    entry_path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-entry.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["hardware_io"] = "gpio"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps([entry]), encoding="utf-8")

    errors = validate_registry(registry_path)

    assert any("agents[0].hardware_io" in error for error in errors)


def test_malformed_root_shape_reports_error(tmp_path: Path) -> None:
    """Root must be array or LiteRegistry object with agents key."""
    path = tmp_path / "bad-root.json"
    path.write_text('{"version": "1.0"}', encoding="utf-8")
    errors = validate_registry(path)
    assert len(errors) == 1
    assert "Root must be either" in errors[0]


def test_missing_required_field_in_entry(tmp_path: Path) -> None:
    """RegistryEntry missing description is reported with agents[i] path."""
    entry_path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-agents-array.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))[0]
    del entry["description"]
    path = tmp_path / "bad-entry.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    errors = validate_registry(path)
    assert errors
    assert any("agents[0]" in err and "description" in err for err in errors)


def test_lite_registry_rejects_duplicate_agent_ids(tmp_path: Path) -> None:
    """CI schema check must reject duplicate URNs (marketplace lookup is first-match)."""
    entry_path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-entry.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    path = tmp_path / "dup-object.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "updated_at": "2026-05-24T00:00:00Z",
                "agents": [entry, entry],
            }
        ),
        encoding="utf-8",
    )
    errors = validate_registry(path)
    assert any("duplicate id" in error for error in errors)
    assert any(entry["id"] in error for error in errors)


def test_agents_array_rejects_duplicate_agent_ids(tmp_path: Path) -> None:
    """Array-form registry must reject the same agent URN twice."""
    entry_path = REGISTRY_FIXTURES_DIR / "shellclaw-v1.0-entry.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    path = tmp_path / "dup-array.json"
    path.write_text(json.dumps([entry, entry]), encoding="utf-8")
    errors = validate_registry(path)
    assert any("duplicate id" in error for error in errors)
    assert any(entry["id"] in error for error in errors)


def test_agents_list_invalid_urn(tmp_path: Path) -> None:
    """Malformed agent id in array format reports agents[i].id."""
    bad = [
        {
            "id": "not-a-urn",
            "name": "Bad",
            "description": "x",
            "endpoints": {"http": "https://example.com/asap"},
            "skills": ["echo"],
            "asap_version": "2.1.0",
        }
    ]
    path = tmp_path / "bad-id.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    errors = validate_registry(path)
    assert errors
    assert any("agents[0]" in err and "id" in err for err in errors)
