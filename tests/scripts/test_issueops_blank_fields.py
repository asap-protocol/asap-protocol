"""Lock IssueOps required-field gates before any registry mutation.

Parse-only tests do not prove ``run`` refuses a blank reason or name. A blank
reason is a valid ``RevokedEntry`` string, so skipping the gate would append a
revocation. A blank removal name must not delete the caller's agent.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.process_removal import run as run_removal
from scripts.process_revocation import run as run_revocation

_URN = "urn:asap:agent:testuser:my-agent"


def _revocation_files(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    registry_path = tmp_path / "registry.json"
    revoked_path = tmp_path / "revoked_agents.json"
    output_path = tmp_path / "result.json"
    revoked_bytes = json.dumps({"revoked": [], "version": "1.0"})
    registry_path.write_text(json.dumps([{"id": _URN, "name": "my-agent"}]))
    revoked_path.write_text(revoked_bytes)
    return registry_path, revoked_path, output_path, revoked_bytes


def _assert_revocation_rejected(
    output_path: Path,
    revoked_path: Path,
    revoked_bytes: str,
) -> dict[str, object]:
    result = json.loads(output_path.read_text())
    assert result["valid"] is False
    assert isinstance(result["debug_id"], str)
    assert result["debug_id"].startswith("ASAP-")
    assert revoked_path.read_text() == revoked_bytes
    return result


def test_revocation_run_rejects_blank_reason(tmp_path: Path) -> None:
    """A registered URN with a whitespace reason must not be appended."""
    registry_path, revoked_path, output_path, revoked_bytes = _revocation_files(tmp_path)
    body = f"### Agent URN\n{_URN}\n\n### Reason\n   \n"

    run_revocation(
        body=body,
        issue_number="11",
        output_path=str(output_path),
        registry_path=str(registry_path),
        revoked_path=str(revoked_path),
    )

    result = _assert_revocation_rejected(output_path, revoked_path, revoked_bytes)
    assert result["errors"] == "Missing required field: Reason"


def test_revocation_run_rejects_fenced_only_reason(tmp_path: Path) -> None:
    """A reason that sanitizes to empty is still a missing reason."""
    registry_path, revoked_path, output_path, revoked_bytes = _revocation_files(tmp_path)
    body = f"### Agent URN\n{_URN}\n\n### Reason\n```\nleaked key\n```\n"

    run_revocation(
        body=body,
        issue_number="12",
        output_path=str(output_path),
        registry_path=str(registry_path),
        revoked_path=str(revoked_path),
    )

    result = _assert_revocation_rejected(output_path, revoked_path, revoked_bytes)
    assert result["errors"] == "Missing required field: Reason"


def test_revocation_run_rejects_blank_urn_and_reason(tmp_path: Path) -> None:
    """Whitespace URN and reason fail closed before the registry lookup."""
    registry_path, revoked_path, output_path, revoked_bytes = _revocation_files(tmp_path)
    body = "### Agent URN\n   \n\n### Reason\n   \n"

    run_revocation(
        body=body,
        issue_number="13",
        output_path=str(output_path),
        registry_path=str(registry_path),
        revoked_path=str(revoked_path),
    )

    result = _assert_revocation_rejected(output_path, revoked_path, revoked_bytes)
    assert result["errors"] == ("Missing required field: Agent URN; Missing required field: Reason")


def test_removal_run_rejects_blank_name(tmp_path: Path) -> None:
    """A whitespace slug must not remove the author's agent."""
    registry_path = tmp_path / "registry.json"
    output_path = tmp_path / "result.json"
    registry_bytes = json.dumps([{"id": _URN, "name": "my-agent"}])
    registry_path.write_text(registry_bytes)
    body = "### Agent name (slug-friendly)\n   \n"

    run_removal(
        body=body,
        issue_number="14",
        author="testuser",
        output_path=str(output_path),
        registry_path=str(registry_path),
    )

    result = json.loads(output_path.read_text())
    assert result["valid"] is False
    assert result["errors"] == "Missing required field: name"
    assert result["debug_id"].startswith("ASAP-")
    assert registry_path.read_text() == registry_bytes
