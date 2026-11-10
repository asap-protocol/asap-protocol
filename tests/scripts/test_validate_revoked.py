"""Tests for scripts/validate_revoked.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_revoked import validate_revoked


def test_validate_revoked_accepts_valid_file(tmp_path: Path) -> None:
    """Valid revoked_agents.json returns no errors."""
    revoked_path = tmp_path / "revoked_agents.json"
    revoked_path.write_text(
        json.dumps(
            {
                "revoked": [
                    {
                        "urn": "urn:asap:agent:testuser:my-agent",
                        "reason": "compromised",
                        "revoked_at": "2025-01-01T00:00:00Z",
                    }
                ],
                "version": "1.0",
            }
        )
    )

    errors = validate_revoked(revoked_path)

    assert errors == []


def test_validate_revoked_rejects_missing_file(tmp_path: Path) -> None:
    """Missing file returns a descriptive error."""
    errors = validate_revoked(tmp_path / "missing.json")

    assert len(errors) == 1
    assert "File not found" in errors[0]


def test_validate_revoked_rejects_invalid_json(tmp_path: Path) -> None:
    """Malformed JSON returns a parse error."""
    revoked_path = tmp_path / "revoked_agents.json"
    revoked_path.write_text("{not valid json")

    errors = validate_revoked(revoked_path)

    assert len(errors) == 1
    assert "Invalid JSON" in errors[0]


def test_validate_revoked_rejects_schema_violation(tmp_path: Path) -> None:
    """Schema violations return field-level validation errors."""
    revoked_path = tmp_path / "revoked_agents.json"
    revoked_path.write_text(json.dumps({"revoked": "not-a-list", "version": "1.0"}))

    errors = validate_revoked(revoked_path)

    assert len(errors) >= 1
    assert any("revoked" in err for err in errors)


@pytest.mark.parametrize("root", [[], None, True, 0, "revoked"])
def test_validate_revoked_rejects_non_object_root(tmp_path: Path, root: Any) -> None:
    """A non-object revocation file is not an empty allow-list.

    ``is_revoked`` ignores a non-object payload. The CI validator must reject
    that shape before it can be published as ``revoked_agents.json``.
    """
    revoked_path = tmp_path / "revoked_agents.json"
    revoked_path.write_text(json.dumps(root))

    errors = validate_revoked(revoked_path)

    assert errors == ["Root must be a JSON object with 'revoked' and 'version'."]
