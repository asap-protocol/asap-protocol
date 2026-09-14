"""Tests for scripts/process_removal.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.process_removal import parse_issue_body, run


def _registry_agents(path: Path) -> list[Any]:
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return raw
    assert isinstance(raw, dict)
    return raw["agents"]


VALID_BODY_REMOVE = """
### Agent name (slug-friendly)
my-agent

### Confirmation
- [x] I confirm that I want to remove this agent from the registry.
"""


class TestParseIssueBodyRemoval:
    def test_parses_valid_body(self) -> None:
        out = parse_issue_body(VALID_BODY_REMOVE)
        assert out["name"] == "my-agent"

    def test_parses_missing_name(self) -> None:
        out = parse_issue_body("### Confirmation\n- [x] I confirm")
        assert "name" not in out


class TestProcessRemovalRun:
    def test_valid_removal(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"

        existing = [
            {"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"},
            {"id": "urn:asap:agent:other:their-agent", "name": "their-agent"},
        ]
        registry_path.write_text(json.dumps(existing))
        output_path = tmp_path / "result.json"

        run(
            body=VALID_BODY_REMOVE,
            issue_number="1",
            author="testuser",
            output_path=str(output_path),
            registry_path=str(registry_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is True

        saved = json.loads(registry_path.read_text())
        assert isinstance(saved, dict)
        assert saved["version"] == "1.0"
        assert isinstance(saved["updated_at"], str) and saved["updated_at"].endswith("Z")
        new_registry = _registry_agents(registry_path)
        assert len(new_registry) == 1
        assert new_registry[0]["id"] == "urn:asap:agent:other:their-agent"

    def test_invalid_removal_unauthorized(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        existing = [{"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"}]
        registry_path.write_text(json.dumps(existing))
        output_path = tmp_path / "result.json"

        # "hacker" trying to delete "my-agent"
        run(
            body=VALID_BODY_REMOVE,
            issue_number="2",
            author="hacker",
            output_path=str(output_path),
            registry_path=str(registry_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "not found in the registry" in result["errors"]

        new_registry = json.loads(registry_path.read_text())
        assert len(new_registry) == 1  # Still exists

    def test_invalid_removal_missing_agent(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        registry_path.write_text("[]")
        output_path = tmp_path / "result.json"

        run(
            body=VALID_BODY_REMOVE,
            issue_number="3",
            author="testuser",
            output_path=str(output_path),
            registry_path=str(registry_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "not found in the registry" in result["errors"]
        assert "debug_id" in result
        assert result["debug_id"].startswith("ASAP-")
