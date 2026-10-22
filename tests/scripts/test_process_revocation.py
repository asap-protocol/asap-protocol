"""Tests for scripts/process_revocation.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.process_revocation import parse_issue_body, run

VALID_BODY_REVOKE = """
### Agent URN
urn:asap:agent:testuser:my-agent

### Reason
Agent compromised / keys leaked

### Confirmation
- [x] I confirm that this revocation request is valid and authorized.
"""


class TestParseIssueBodyRevocation:
    def test_parses_valid_body(self) -> None:
        out = parse_issue_body(VALID_BODY_REVOKE)
        assert out["urn"] == "urn:asap:agent:testuser:my-agent"
        assert "compromised" in out["reason"]

    def test_parses_missing_urn(self) -> None:
        out = parse_issue_body("### Reason\ncompromised\n\n### Confirmation\n- [x] ok")
        assert "urn" not in out

    def test_parses_missing_reason(self) -> None:
        out = parse_issue_body("### Agent URN\nurn:asap:agent:x\n\n### Confirmation\n- [x] ok")
        assert "reason" not in out or not out.get("reason")


class TestProcessRevocationRun:
    def test_valid_revocation_appends_to_revoked(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"

        registry_path.write_text(
            json.dumps(
                [
                    {"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"},
                    {"id": "urn:asap:agent:other:their-agent", "name": "their-agent"},
                ]
            )
        )
        revoked_path.write_text(json.dumps({"revoked": [], "version": "1.0"}))

        run(
            body=VALID_BODY_REVOKE,
            issue_number="1",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is True

        revoked_data = json.loads(revoked_path.read_text())
        assert len(revoked_data["revoked"]) == 1
        assert revoked_data["revoked"][0]["urn"] == "urn:asap:agent:testuser:my-agent"
        assert "compromised" in revoked_data["revoked"][0]["reason"]
        assert "revoked_at" in revoked_data["revoked"][0]

    def test_urn_not_in_registry_fails(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"

        registry_path.write_text(json.dumps([]))
        revoked_path.write_text(json.dumps({"revoked": [], "version": "1.0"}))

        run(
            body=VALID_BODY_REVOKE,
            issue_number="2",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "not found in the registry" in result["errors"]
        assert "debug_id" in result
        assert result["debug_id"].startswith("ASAP-")

        revoked_data = json.loads(revoked_path.read_text())
        assert len(revoked_data["revoked"]) == 0

    def test_already_revoked_fails(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"

        registry_path.write_text(
            json.dumps([{"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"}])
        )
        revoked_path.write_text(
            json.dumps(
                {
                    "revoked": [
                        {
                            "urn": "urn:asap:agent:testuser:my-agent",
                            "reason": "previous",
                            "revoked_at": "2025-01-01T00:00:00Z",
                        }
                    ],
                    "version": "1.0",
                }
            )
        )

        run(
            body=VALID_BODY_REVOKE,
            issue_number="3",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "already in the revocation list" in result["errors"]

    def test_result_json_written_on_success(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"

        registry_path.write_text(
            json.dumps([{"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"}])
        )
        revoked_path.write_text(json.dumps({"revoked": [], "version": "1.0"}))

        run(
            body=VALID_BODY_REVOKE,
            issue_number="4",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert "valid" in result
        assert result["valid"] is True

    def test_rejects_urn_that_is_not_an_agent_urn(self, tmp_path: Path) -> None:
        """A URN that is not an agent URN is rejected before the list is rewritten."""
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"
        bad_id = "urn:asap:user:alice"
        original_revoked = json.dumps({"revoked": [], "version": "1.0"})

        registry_path.write_text(json.dumps([{"id": bad_id, "name": "not-an-agent-urn"}]))
        revoked_path.write_text(original_revoked)

        run(
            body=f"""
### Agent URN
{bad_id}

### Reason
compromised

### Confirmation
- [x] I confirm that this revocation request is valid and authorized.
""",
            issue_number="5",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "Invalid URN format" in result["errors"]
        assert bad_id in result["errors"]
        assert revoked_path.read_text() == original_revoked

    def test_invalid_revoked_schema_is_not_rewritten(self, tmp_path: Path) -> None:
        """A revoked file that fails schema validation is left byte-for-byte unchanged."""
        registry_path = tmp_path / "registry.json"
        revoked_path = tmp_path / "revoked_agents.json"
        output_path = tmp_path / "result.json"
        original_revoked = json.dumps(
            {"revoked": [{"urn": "urn:asap:agent:testuser:my-agent"}], "version": "1.0"}
        )

        registry_path.write_text(
            json.dumps([{"id": "urn:asap:agent:testuser:my-agent", "name": "my-agent"}])
        )
        revoked_path.write_text(original_revoked)

        run(
            body=VALID_BODY_REVOKE,
            issue_number="6",
            output_path=str(output_path),
            registry_path=str(registry_path),
            revoked_path=str(revoked_path),
        )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "revoked_agents.json schema invalid" in result["errors"]
        assert revoked_path.read_text() == original_revoked
