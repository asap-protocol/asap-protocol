"""Tests for ASAP CLI."""

import asyncio
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from asap import __version__
from asap.cli import app
from asap.cli.schemas import DEFAULT_SCHEMAS_DIR
from asap.crypto.keys import generate_keypair, serialize_private_key
from asap.economics.delegation_storage import SQLiteDelegationStorage
from asap.schemas import TOTAL_SCHEMA_COUNT

runner = CliRunner()

# ANSI escape sequence pattern for stripping colors from output
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


class TestCliVersion:
    """Tests for CLI version command."""

    def test_version_flag(self) -> None:
        """Ensure --version prints the package version."""
        runner = CliRunner()
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert result.stdout.strip() == __version__


class TestCliHelp:
    """Tests for CLI help output."""

    def test_help_displays_commands(self) -> None:
        """Ensure --help shows available commands."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "export-schemas" in output
        assert "list-schemas" in output
        assert "show-schema" in output

    def test_export_schemas_help(self) -> None:
        """Ensure export-schemas --help shows options."""
        runner = CliRunner()
        result = runner.invoke(app, ["export-schemas", "--help"])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--output-dir" in output


class TestCliExportSchemas:
    """Tests for export-schemas command."""

    def test_writes_schema_files(self, tmp_path: Path) -> None:
        """Ensure export-schemas writes schema files."""
        runner = CliRunner()
        result = runner.invoke(app, ["export-schemas", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / "entities" / "agent.schema.json").exists()
        assert (tmp_path / "payloads" / "task_request.schema.json").exists()
        assert (tmp_path / "envelope.schema.json").exists()

    def test_reports_count(self, tmp_path: Path) -> None:
        """Ensure export-schemas reports the number of exported schemas."""
        runner = CliRunner()
        result = runner.invoke(app, ["export-schemas", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert f"Exported {TOTAL_SCHEMA_COUNT} schemas" in result.stdout

    def test_verbose_lists_exported_files(self, tmp_path: Path) -> None:
        """Ensure export-schemas with -v (global) lists each exported file."""
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "export-schemas", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "agent.schema.json" in result.stdout or "envelope.schema.json" in result.stdout

    def test_handles_permission_error(self, tmp_path: Path) -> None:
        """Ensure export-schemas handles permission errors gracefully."""
        # Patch the symbol the command actually calls; schemas.py imports
        # export_all_schemas from asap.schemas directly.
        with patch("asap.cli.schemas.export_all_schemas") as mock_export:
            mock_export.side_effect = PermissionError("Access denied")
            runner = CliRunner()
            result = runner.invoke(app, ["export-schemas", "--output-dir", str(tmp_path)])

            assert result.exit_code != 0
            # Typer outputs error messages to stdout via echo
            assert "Cannot write to directory" in result.output

    def test_handles_os_error(self, tmp_path: Path) -> None:
        """Ensure export-schemas handles OS errors gracefully."""
        with patch("asap.cli.schemas.export_all_schemas") as mock_export:
            mock_export.side_effect = OSError("Disk full")
            runner = CliRunner()
            result = runner.invoke(app, ["export-schemas", "--output-dir", str(tmp_path)])

            assert result.exit_code != 0
            # Typer outputs error messages to stdout via echo
            assert "Failed to export schemas" in result.output

    def test_validates_exported_content(self, tmp_path: Path) -> None:
        """Ensure exported schemas contain expected structure."""
        runner = CliRunner()
        result = runner.invoke(app, ["export-schemas", "--output-dir", str(tmp_path)])

        assert result.exit_code == 0

        # Spot check a few schemas
        agent_schema = json.loads((tmp_path / "entities" / "agent.schema.json").read_text())
        assert "id" in agent_schema.get("properties", {})

        envelope_schema = json.loads((tmp_path / "envelope.schema.json").read_text())
        assert "asap_version" in envelope_schema.get("properties", {})


class TestCliListSchemas:
    """Tests for list-schemas command."""

    def test_prints_known_schema_names(self) -> None:
        """Ensure list-schemas prints known schema names."""
        runner = CliRunner()
        result = runner.invoke(app, ["list-schemas"])

        assert result.exit_code == 0
        assert "agent\tentities/agent.schema.json" in result.stdout

    def test_lists_all_schemas(self) -> None:
        """Ensure list-schemas lists all registered schemas."""
        runner = CliRunner()
        result = runner.invoke(app, ["list-schemas"])

        assert result.exit_code == 0
        assert "envelope" in result.stdout
        assert "task_request" in result.stdout
        assert "manifest" in result.stdout


class TestCliShowSchema:
    """Tests for show-schema command."""

    def test_prints_valid_json(self) -> None:
        """Ensure show-schema prints valid JSON."""
        runner = CliRunner()
        result = runner.invoke(app, ["show-schema", "agent"])

        assert result.exit_code == 0
        schema = json.loads(result.stdout)
        assert schema["title"] == "Agent"

    def test_rejects_unknown_name(self) -> None:
        """Ensure show-schema rejects unknown schema names."""
        runner = CliRunner()
        result = runner.invoke(app, ["show-schema", "not-a-schema"])

        assert result.exit_code != 0

    def test_envelope_schema(self) -> None:
        """Ensure show-schema works for envelope."""
        runner = CliRunner()
        result = runner.invoke(app, ["show-schema", "envelope"])

        assert result.exit_code == 0
        schema = json.loads(result.stdout)
        assert schema["title"] == "Envelope"


class TestCliDefaults:
    """Tests for CLI default values."""

    def test_default_schemas_dir_exists(self) -> None:
        """Ensure DEFAULT_SCHEMAS_DIR is defined."""
        assert Path("schemas") == DEFAULT_SCHEMAS_DIR


class TestCliKeysGenerate:
    """Tests for keys generate command."""

    def test_keys_generate_writes_pem_file(self, tmp_path: Path) -> None:
        """Ensure keys generate creates a PEM file with mode 0600."""
        key_file = tmp_path / "key.pem"
        runner = CliRunner()
        result = runner.invoke(app, ["keys", "generate", "--out", str(key_file)])

        assert result.exit_code == 0
        assert key_file.exists()
        assert key_file.read_bytes().startswith(b"-----BEGIN")
        assert oct(key_file.stat().st_mode)[-3:] == "600"

    def test_keys_generate_rejects_directory_as_output(self, tmp_path: Path) -> None:
        """Ensure keys generate rejects when --out is a directory."""
        runner = CliRunner()
        result = runner.invoke(app, ["keys", "generate", "--out", str(tmp_path)])

        assert result.exit_code != 0
        assert "directory" in result.output.lower()

    def test_keys_generate_chmod_warning_on_os_error(self, tmp_path: Path) -> None:
        """Ensure keys generate warns when chmod fails but still succeeds."""
        key_file = tmp_path / "key.pem"
        with patch("pathlib.Path.chmod") as mock_chmod:
            mock_chmod.side_effect = OSError("Permission denied")
            runner = CliRunner()
            result = runner.invoke(app, ["keys", "generate", "--out", str(key_file)])

        assert result.exit_code == 0
        assert key_file.exists()
        assert "Warning" in result.output or "0600" in result.output


class TestCliManifestSign:
    """Tests for manifest sign command."""

    def test_manifest_sign_writes_signed_manifest_to_file(self, tmp_path: Path) -> None:
        """Ensure manifest sign produces valid signed manifest."""
        private_key, _ = generate_keypair()
        key_file = tmp_path / "key.pem"
        key_file.write_bytes(serialize_private_key(private_key))
        key_file.chmod(0o600)

        manifest_json = {
            "id": "urn:asap:agent:test",
            "name": "Test",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "echo", "description": "Echo"}],
                "state_persistence": False,
                "streaming": False,
                "mcp_tools": [],
            },
            "endpoints": {"asap": "https://example.com/asap", "events": None},
            "auth": None,
            "signature": None,
            "ttl_seconds": 300,
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_json), encoding="utf-8")

        out_file = tmp_path / "signed.json"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "manifest",
                "sign",
                "--key",
                str(key_file),
                str(manifest_file),
                "--out",
                str(out_file),
            ],
        )

        assert result.exit_code == 0
        assert out_file.exists()
        signed = json.loads(out_file.read_text())
        assert "manifest" in signed
        assert "signature" in signed

    def test_manifest_sign_outputs_to_stdout_when_no_out(self, tmp_path: Path) -> None:
        """Ensure manifest sign prints to stdout when --out not specified."""
        private_key, _ = generate_keypair()
        key_file = tmp_path / "key.pem"
        key_file.write_bytes(serialize_private_key(private_key))
        key_file.chmod(0o600)

        manifest_json = {
            "id": "urn:asap:agent:test",
            "name": "Test",
            "version": "1.0.0",
            "description": "Test",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "echo", "description": "Echo"}],
                "state_persistence": False,
                "streaming": False,
                "mcp_tools": [],
            },
            "endpoints": {"asap": "https://example.com/asap", "events": None},
            "auth": None,
            "signature": None,
            "ttl_seconds": 300,
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_json), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["manifest", "sign", "--key", str(key_file), str(manifest_file)]
        )

        assert result.exit_code == 0
        signed = json.loads(result.stdout)
        assert "manifest" in signed
        assert "signature" in signed

    def test_manifest_sign_rejects_missing_key(self, tmp_path: Path) -> None:
        """Ensure manifest sign rejects when key file missing."""
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text("{}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["manifest", "sign", "--key", "/nonexistent/key.pem", str(manifest_file)]
        )

        assert result.exit_code != 0
        assert "key" in result.output.lower() or "not found" in result.output.lower()

    def test_manifest_sign_rejects_missing_manifest_file(self, tmp_path: Path) -> None:
        """Ensure manifest sign rejects when manifest file missing."""
        private_key, _ = generate_keypair()
        key_file = tmp_path / "key.pem"
        key_file.write_bytes(serialize_private_key(private_key))

        runner = CliRunner()
        result = runner.invoke(
            app, ["manifest", "sign", "--key", str(key_file), "/nonexistent/manifest.json"]
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_manifest_sign_rejects_invalid_json(self, tmp_path: Path) -> None:
        """Ensure manifest sign rejects invalid JSON in manifest file."""
        private_key, _ = generate_keypair()
        key_file = tmp_path / "key.pem"
        key_file.write_bytes(serialize_private_key(private_key))

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text("{invalid json}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["manifest", "sign", "--key", str(key_file), str(manifest_file)]
        )

        assert result.exit_code != 0
        assert "Invalid JSON" in result.output

    def test_manifest_sign_rejects_invalid_manifest_schema(self, tmp_path: Path) -> None:
        """Ensure manifest sign rejects manifest that fails schema validation."""
        private_key, _ = generate_keypair()
        key_file = tmp_path / "key.pem"
        key_file.write_bytes(serialize_private_key(private_key))

        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps({"id": "invalid", "name": "x"}), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["manifest", "sign", "--key", str(key_file), str(manifest_file)]
        )

        assert result.exit_code != 0
        assert "Invalid manifest" in result.output or "validation" in result.output.lower()


class TestCliManifestVerify:
    """Tests for manifest verify command."""

    FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

    def test_manifest_verify_succeeds_with_valid_signed_manifest(self) -> None:
        """Ensure manifest verify passes for valid signed manifest."""
        manifest_path = self.FIXTURES_DIR / "verified_manifest.json"
        assert manifest_path.exists()

        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "verify", str(manifest_path)])

        assert result.exit_code == 0
        assert "valid" in result.output.lower() or "Signature valid" in result.output

    def test_manifest_verify_rejects_missing_file(self) -> None:
        """Ensure manifest verify rejects when file not found."""
        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "verify", "/nonexistent/signed.json"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_manifest_verify_rejects_invalid_json(self, tmp_path: Path) -> None:
        """Ensure manifest verify rejects invalid JSON."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "verify", str(bad_file)])

        assert result.exit_code != 0
        assert "Invalid JSON" in result.output


class TestCliValidateSchema:
    """Tests for validate-schema command."""

    def test_help_shows_validate_schema(self) -> None:
        """Ensure --help shows validate-schema command."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "validate-schema" in output

    def test_validate_schema_help(self) -> None:
        """Ensure validate-schema --help shows options."""
        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", "--help"])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--schema-type" in output
        assert "FILE" in output

    def test_validates_valid_agent_json(self, tmp_path: Path) -> None:
        """Ensure validate-schema accepts valid agent JSON."""
        agent_json = {
            "id": "urn:asap:agent:test-agent",
            "manifest_uri": "https://example.com/.well-known/asap/manifest.json",
            "capabilities": ["task.execute"],
        }
        json_file = tmp_path / "agent.json"
        json_file.write_text(json.dumps(agent_json), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", str(json_file), "--schema-type", "agent"])

        assert result.exit_code == 0
        assert "Valid" in result.stdout

    def test_validates_valid_envelope_json(self, tmp_path: Path) -> None:
        """Ensure validate-schema accepts valid envelope JSON."""
        envelope_json = {
            "asap_version": "0.1",
            "sender": "urn:asap:agent:test-agent",
            "recipient": "urn:asap:agent:target-agent",
            "payload_type": "TaskRequest",
            "payload": {
                "conversation_id": "conv-01",
                "skill_id": "echo",
                "input": {},
            },
        }
        json_file = tmp_path / "envelope.json"
        json_file.write_text(json.dumps(envelope_json), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["validate-schema", str(json_file), "--schema-type", "envelope"]
        )

        assert result.exit_code == 0
        assert "Valid" in result.stdout

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        """Ensure validate-schema rejects invalid JSON syntax."""
        json_file = tmp_path / "invalid.json"
        json_file.write_text("{invalid json}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", str(json_file), "--schema-type", "agent"])

        assert result.exit_code != 0
        assert "Invalid JSON" in result.output

    def test_rejects_invalid_agent_data(self, tmp_path: Path) -> None:
        """Ensure validate-schema rejects data not matching agent schema."""
        invalid_agent = {
            "name": "Missing required id field",
        }
        json_file = tmp_path / "invalid_agent.json"
        json_file.write_text(json.dumps(invalid_agent), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", str(json_file), "--schema-type", "agent"])

        assert result.exit_code != 0
        assert "Validation error" in result.output

    def test_rejects_unknown_schema_type(self, tmp_path: Path) -> None:
        """Ensure validate-schema rejects unknown schema types."""
        json_file = tmp_path / "test.json"
        json_file.write_text("{}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["validate-schema", str(json_file), "--schema-type", "unknown_type"]
        )

        assert result.exit_code != 0
        assert "Unknown schema type" in result.output

    def test_handles_missing_file(self) -> None:
        """Ensure validate-schema handles missing files gracefully."""
        runner = CliRunner()
        result = runner.invoke(
            app, ["validate-schema", "/nonexistent/file.json", "--schema-type", "agent"]
        )

        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_rejects_json_root_not_object(self, tmp_path: Path) -> None:
        """Ensure validate-schema rejects JSON with root not a dict (e.g. array)."""
        json_file = tmp_path / "array.json"
        json_file.write_text("[1, 2, 3]", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", str(json_file), "--schema-type", "agent"])

        assert result.exit_code != 0
        assert (
            "object" in result.output.lower()
            or "root" in result.output.lower()
            or "JSON" in result.output
        )

    def test_auto_detects_envelope_schema(self, tmp_path: Path) -> None:
        """Ensure validate-schema can auto-detect envelope schema from payload_type."""
        envelope_json = {
            "asap_version": "0.1",
            "sender": "urn:asap:agent:test-agent",
            "recipient": "urn:asap:agent:target-agent",
            "payload_type": "TaskRequest",
            "payload": {
                "conversation_id": "conv-01",
                "skill_id": "echo",
                "input": {},
            },
        }
        json_file = tmp_path / "envelope.json"
        json_file.write_text(json.dumps(envelope_json), encoding="utf-8")

        runner = CliRunner()
        # No --schema-type provided, should auto-detect as envelope
        result = runner.invoke(app, ["validate-schema", str(json_file)])

        assert result.exit_code == 0
        assert "Valid" in result.stdout

    def test_requires_schema_type_for_non_envelope(self, tmp_path: Path) -> None:
        """Ensure validate-schema requires schema-type for non-envelope JSON."""
        agent_json = {
            "id": "urn:asap:agent:test-agent",
            "manifest_uri": "https://example.com/manifest.json",
            "capabilities": [],
        }
        json_file = tmp_path / "agent.json"
        json_file.write_text(json.dumps(agent_json), encoding="utf-8")

        runner = CliRunner()
        # No --schema-type provided, not auto-detectable
        result = runner.invoke(app, ["validate-schema", str(json_file)])

        assert result.exit_code != 0
        # Strip ANSI codes before checking output
        clean_output = strip_ansi(result.output).lower()
        assert "schema-type" in clean_output

    def test_displays_validation_error_details(self, tmp_path: Path) -> None:
        """Ensure validate-schema shows detailed validation errors."""
        invalid_agent = {
            "id": "invalid-ulid-format",  # Invalid ULID format
            "name": "Test Agent",
        }
        json_file = tmp_path / "invalid_agent.json"
        json_file.write_text(json.dumps(invalid_agent), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["validate-schema", str(json_file), "--schema-type", "agent"])

        assert result.exit_code != 0
        # Should contain some field information in the error
        assert "id" in result.output.lower() or "validation" in result.output.lower()


class TestCliTrace:
    """Tests for trace command."""

    def test_help_shows_trace_command(self) -> None:
        """Ensure --help shows trace command."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "trace" in output

    def test_trace_requires_trace_id(self) -> None:
        """Ensure trace command requires trace_id argument."""
        runner = CliRunner()
        result = runner.invoke(app, ["trace"])
        assert result.exit_code != 0
        assert "trace_id" in result.output.lower() or "required" in result.output.lower()

    def test_trace_from_log_file(self, tmp_path: Path) -> None:
        """Ensure trace parses log file and prints ASCII diagram."""
        log_lines = [
            '{"event": "asap.request.received", "envelope_id": "e1", "trace_id": "trace-abc", '
            '"sender": "urn:asap:agent:client", "recipient": "urn:asap:agent:echo", "timestamp": "2026-01-31T12:00:00Z"}',
            '{"event": "asap.request.processed", "envelope_id": "e1", "trace_id": "trace-abc", "duration_ms": 14}',
        ]
        log_file = tmp_path / "asap.log"
        log_file.write_text("\n".join(log_lines), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["trace", "trace-abc", "--log-file", str(log_file)])

        assert result.exit_code == 0
        assert "client -> echo (14ms)" in result.stdout

    def test_trace_format_json_outputs_structured_json(self, tmp_path: Path) -> None:
        """Ensure trace --format json outputs valid JSON for external tools."""
        log_lines = [
            '{"event": "asap.request.received", "envelope_id": "e1", "trace_id": "trace-xyz", '
            '"sender": "urn:asap:agent:a", "recipient": "urn:asap:agent:b", "timestamp": "2026-01-31T12:00:00Z"}',
            '{"event": "asap.request.processed", "envelope_id": "e1", "trace_id": "trace-xyz", "duration_ms": 42}',
        ]
        log_file = tmp_path / "asap.log"
        log_file.write_text("\n".join(log_lines), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app, ["trace", "trace-xyz", "--log-file", str(log_file), "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["trace_id"] == "trace-xyz"
        assert len(data["hops"]) == 1
        assert data["hops"][0]["sender"] == "urn:asap:agent:a"
        assert data["hops"][0]["recipient"] == "urn:asap:agent:b"
        assert data["hops"][0]["duration_ms"] == 42

    def test_trace_invalid_format_fails(self) -> None:
        """Ensure trace rejects invalid --format values."""
        runner = CliRunner()
        result = runner.invoke(app, ["trace", "t1", "--format", "xml"])
        assert result.exit_code != 0
        assert "ascii" in result.output.lower() or "json" in result.output.lower()

    def test_trace_no_match_exits_non_zero(self, tmp_path: Path) -> None:
        """Ensure trace exits with 1 when no trace found."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["trace", "nonexistent", "--log-file", str(log_file)])

        assert result.exit_code != 0
        assert "No trace found" in result.stdout

    def test_trace_missing_log_file_fails(self) -> None:
        """Ensure trace fails when --log-file path does not exist."""
        runner = CliRunner()
        result = runner.invoke(app, ["trace", "t1", "--log-file", "/nonexistent/asap.log"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "No such" in result.output


class TestCliManifestInfo:
    """Tests for manifest info command (trust level display)."""

    FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

    def test_manifest_info_shows_trust_level_verified(self) -> None:
        manifest_path = self.FIXTURES_DIR / "verified_manifest.json"
        assert manifest_path.exists(), "Fixture verified_manifest.json required"

        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "info", str(manifest_path)])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "Trust level: verified" in output
        assert "Manifest ID:" in output
        assert "Name:" in output

    def test_manifest_info_shows_trust_level_self_signed(self) -> None:
        manifest_path = self.FIXTURES_DIR / "self_signed_manifest.json"
        assert manifest_path.exists(), "Fixture self_signed_manifest.json required"

        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "info", str(manifest_path)])

        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "Trust level: self-signed" in output

    def test_manifest_info_rejects_missing_file(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "info", "/nonexistent/manifest.json"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "File not found" in result.output

    def test_manifest_info_rejects_invalid_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["manifest", "info", str(bad_file)])

        assert result.exit_code != 0
        assert "Invalid JSON" in result.output


class TestCliRepl:
    """Tests for repl command."""

    def test_help_shows_repl_command(self) -> None:
        """Ensure --help shows repl command."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "repl" in output

    def test_repl_help(self) -> None:
        """Ensure repl --help shows usage."""
        runner = CliRunner()
        result = runner.invoke(app, ["repl", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "REPL" in output or "interactive" in output.lower()

    def test_repl_starts_and_exits(self) -> None:
        """Ensure repl starts and exits when given exit() via stdin."""
        runner = CliRunner()
        result = runner.invoke(app, ["repl"], input="exit()\n")
        assert result.exit_code == 0
        # Banner may go to stdout or stderr depending on code.interact()
        combined = result.stdout + result.stderr
        assert "REPL" in combined or "sample_envelope" in combined or ">>> " in combined


class TestCliDelegationRevoke:
    """Tests for delegation revoke command."""

    def test_revoke_writes_to_db_and_echoes(self, tmp_path: Path) -> None:
        """Revoke writes to DB and echoes message."""
        db_path = tmp_path / "revoke.db"
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["delegation", "revoke", "del_test_123", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Revoked delegation token: del_test_123" in result.stdout
        storage = SQLiteDelegationStorage(db_path=db_path)
        assert asyncio.run(storage.is_revoked("del_test_123")) is True

    def test_revoke_with_reason(self, tmp_path: Path) -> None:
        """Revoke accepts --reason."""
        db_path = tmp_path / "revoke_reason.db"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "delegation",
                "revoke",
                "del_r",
                "--db",
                str(db_path),
                "--reason",
                "compromised",
            ],
        )
        assert result.exit_code == 0
        assert asyncio.run(SQLiteDelegationStorage(db_path=db_path).is_revoked("del_r")) is True


# ---------------------------------------------------------------------------
# CLI Coverage Tests (merged from test_cli_coverage.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def keypair_files(tmp_path: Path) -> tuple[Path, Path]:
    """Generate a keypair and write the private key PEM to tmp_path."""
    priv, pub = generate_keypair()
    key_file = tmp_path / "test_key.pem"
    key_file.write_bytes(serialize_private_key(priv))
    key_file.chmod(0o600)
    return key_file, tmp_path


# ---------------------------------------------------------------------------
# manifest info
# ---------------------------------------------------------------------------


class TestManifestInfo:
    """Cover manifest info command."""

    def test_manifest_info_file_not_found(self) -> None:
        result = runner.invoke(app, ["manifest", "info", "/nonexistent/file.json"])
        assert result.exit_code != 0

    def test_manifest_info_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json", encoding="utf-8")
        result = runner.invoke(app, ["manifest", "info", str(bad)])
        assert result.exit_code != 0

    def test_manifest_info_invalid_format(self, tmp_path: Path) -> None:
        """Invalid signed manifest format triggers BadParameter (lines 148-149)."""
        bad = tmp_path / "invalid_manifest.json"
        bad.write_text('{"manifest": "not the right format"}', encoding="utf-8")
        result = runner.invoke(app, ["manifest", "info", str(bad)])
        assert result.exit_code != 0

    def test_manifest_info_valid(self, keypair_files: tuple[Path, Path]) -> None:
        """Full valid flow: sign then info."""
        key_file, tmp_path = keypair_files
        from asap.crypto.keys import load_private_key_from_file_sync
        from asap.crypto.signing import sign_manifest
        from asap.models.entities import Capability, Endpoint, Manifest, Skill

        priv = load_private_key_from_file_sync(key_file)
        manifest = Manifest(
            id="urn:asap:agent:test-info",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="echo", description="Echo skill")],
            ),
            endpoints=Endpoint(asap="https://example.com"),
        )
        signed = sign_manifest(manifest, priv)
        sm_file = tmp_path / "signed.json"
        sm_file.write_text(json.dumps(signed.model_dump()), encoding="utf-8")

        result = runner.invoke(app, ["manifest", "info", str(sm_file)])
        assert result.exit_code == 0
        assert "Manifest ID" in result.output


# ---------------------------------------------------------------------------
# manifest verify
# ---------------------------------------------------------------------------


class TestManifestVerify:
    """Cover manifest verify error paths."""

    def test_verify_file_not_found(self) -> None:
        result = runner.invoke(app, ["manifest", "verify", "/nonexistent"])
        assert result.exit_code != 0

    def test_verify_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json}", encoding="utf-8")
        result = runner.invoke(app, ["manifest", "verify", str(bad)])
        assert result.exit_code != 0

    def test_verify_invalid_format(self, tmp_path: Path) -> None:
        """Invalid signed manifest format (line 184-185)."""
        bad = tmp_path / "bad.json"
        bad.write_text('{"x": 1}', encoding="utf-8")
        result = runner.invoke(app, ["manifest", "verify", str(bad)])
        assert result.exit_code != 0

    def test_verify_public_key_not_found(self, keypair_files: tuple[Path, Path]) -> None:
        """--public-key path does not exist (lines 188-189)."""
        key_file, tmp_path = keypair_files
        from asap.crypto.keys import load_private_key_from_file_sync
        from asap.crypto.signing import sign_manifest
        from asap.models.entities import Capability, Endpoint, Manifest, Skill

        priv = load_private_key_from_file_sync(key_file)
        manifest = Manifest(
            id="urn:asap:agent:test-verify",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="echo", description="Echo skill")],
            ),
            endpoints=Endpoint(asap="https://example.com"),
        )
        signed = sign_manifest(manifest, priv)
        sm_file = tmp_path / "signed.json"
        sm_file.write_text(json.dumps(signed.model_dump()), encoding="utf-8")

        result = runner.invoke(
            app,
            ["manifest", "verify", str(sm_file), "--public-key", "/nonexistent/key.pem"],
        )
        assert result.exit_code != 0

    def test_verify_signature_invalid(self, keypair_files: tuple[Path, Path]) -> None:
        """Signature verification failure (lines 194-196)."""
        key_file, tmp_path = keypair_files
        from asap.crypto.keys import load_private_key_from_file_sync
        from asap.crypto.signing import sign_manifest
        from asap.models.entities import Capability, Endpoint, Manifest, Skill

        priv = load_private_key_from_file_sync(key_file)
        manifest = Manifest(
            id="urn:asap:agent:test-verify",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="echo", description="Echo skill")],
            ),
            endpoints=Endpoint(asap="https://example.com"),
        )
        signed = sign_manifest(manifest, priv)
        # Tamper with signature
        data = signed.model_dump()
        data["signature"] = "aW52YWxpZA=="  # base64("invalid")
        sm_file = tmp_path / "signed.json"
        sm_file.write_text(json.dumps(data), encoding="utf-8")

        result = runner.invoke(app, ["manifest", "verify", str(sm_file)])
        assert result.exit_code != 0

    def test_verify_valid_with_public_key(self, keypair_files: tuple[Path, Path]) -> None:
        """Successful verification using --public-key (lines 190-191, 197-200)."""
        key_file, tmp_path = keypair_files
        from asap.crypto.keys import load_private_key_from_file_sync
        from asap.crypto.signing import sign_manifest
        from asap.models.entities import Capability, Endpoint, Manifest, Skill

        priv = load_private_key_from_file_sync(key_file)
        manifest = Manifest(
            id="urn:asap:agent:test-verify2",
            name="Test Agent 2",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="echo", description="Echo skill")],
            ),
            endpoints=Endpoint(asap="https://example.com"),
        )
        signed = sign_manifest(manifest, priv)
        sm_file = tmp_path / "signed2.json"
        sm_file.write_text(json.dumps(signed.model_dump()), encoding="utf-8")

        result = runner.invoke(
            app,
            ["manifest", "verify", str(sm_file), "--public-key", str(key_file)],
        )
        assert result.exit_code == 0
        assert "Signature valid" in result.output


# ---------------------------------------------------------------------------
# delegation create
# ---------------------------------------------------------------------------


class TestDelegationCreate:
    """Cover delegation create command (lines 213-245)."""

    def test_delegation_create_key_not_found(self) -> None:
        result = runner.invoke(
            app,
            [
                "delegation",
                "create",
                "--delegate",
                "urn:del:x",
                "--scopes",
                "scope1",
                "--key",
                "/nonexistent/key.pem",
                "--delegator",
                "urn:d:a",
            ],
        )
        assert result.exit_code != 0

    def test_delegation_create_key_is_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "delegation",
                "create",
                "--delegate",
                "urn:del:x",
                "--scopes",
                "scope1",
                "--key",
                str(tmp_path),
                "--delegator",
                "urn:d:a",
            ],
        )
        assert result.exit_code != 0

    def test_delegation_create_empty_scopes(self, keypair_files: tuple[Path, Path]) -> None:
        key_file, _ = keypair_files
        result = runner.invoke(
            app,
            [
                "delegation",
                "create",
                "--delegate",
                "urn:del:x",
                "--scopes",
                ",,,",
                "--key",
                str(key_file),
                "--delegator",
                "urn:d:a",
            ],
        )
        assert result.exit_code != 0

    def test_delegation_create_success(self, keypair_files: tuple[Path, Path]) -> None:
        """Successful delegation token creation."""
        key_file, _ = keypair_files
        result = runner.invoke(
            app,
            [
                "delegation",
                "create",
                "--delegate",
                "urn:del:x",
                "--scopes",
                "task.request,task.response",
                "--key",
                str(key_file),
                "--delegator",
                "urn:d:a",
                "--expires-in",
                "3600",
                "--max-tasks",
                "10",
            ],
        )
        assert result.exit_code == 0
        # Output should be a JWT string (3 dot-separated parts)
        token = result.output.strip()
        assert token.count(".") == 2

    def test_delegation_create_open_permissions_warning(
        self, keypair_files: tuple[Path, Path]
    ) -> None:
        """Key file with open permissions emits warning (lines 218-224)."""
        key_file, _ = keypair_files
        key_file.chmod(0o644)  # Open permissions
        result = runner.invoke(
            app,
            [
                "delegation",
                "create",
                "--delegate",
                "urn:del:x",
                "--scopes",
                "scope1",
                "--key",
                str(key_file),
                "--delegator",
                "urn:d:a",
            ],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _repl_namespace
# ---------------------------------------------------------------------------


class TestReplNamespace:
    """Cover _repl_namespace and sample_envelope (line 471)."""

    def test_namespace_contains_expected_keys(self) -> None:
        from asap.cli.repl import _repl_namespace

        ns = _repl_namespace()
        assert "Envelope" in ns
        assert "TaskRequest" in ns
        assert "Manifest" in ns
        assert "generate_id" in ns
        assert "sample_envelope" in ns

    def test_sample_envelope_produces_valid_envelope(self) -> None:
        from asap.cli.repl import _repl_namespace

        ns = _repl_namespace()
        envelope = ns["sample_envelope"]()
        assert envelope.payload_type == "task.request"
        assert envelope.sender == "urn:asap:agent:repl-sender"


# ---------------------------------------------------------------------------
# trace command — env var and json format
# ---------------------------------------------------------------------------


class TestTraceEnvAndJson:
    """Cover trace with ASAP_TRACE_LOG env var (line 543) and json format."""

    def test_trace_json_format(self, tmp_path: Path) -> None:
        log_lines = (
            '{"event": "asap.request.received", "envelope_id": "e1", '
            '"trace_id": "tr-1", "sender": "urn:asap:agent:a", '
            '"recipient": "urn:asap:agent:b", "timestamp": "2026-01-31T12:00:00Z"}\n'
            '{"event": "asap.request.processed", "envelope_id": "e1", '
            '"trace_id": "tr-1", "duration_ms": 10}\n'
        )
        log_file = tmp_path / "trace.log"
        log_file.write_text(log_lines, encoding="utf-8")

        result = runner.invoke(
            app, ["trace", "tr-1", "--log-file", str(log_file), "--format", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["trace_id"] == "tr-1"

    def test_trace_env_var_log_file(self, tmp_path: Path) -> None:
        """ASAP_TRACE_LOG env var specifies default log file (line 543)."""
        log_lines = (
            '{"event": "asap.request.received", "envelope_id": "e1", '
            '"trace_id": "tr-env", "sender": "urn:asap:agent:a", '
            '"recipient": "urn:asap:agent:b", "timestamp": "2026-01-31T12:00:00Z"}\n'
            '{"event": "asap.request.processed", "envelope_id": "e1", '
            '"trace_id": "tr-env", "duration_ms": 5}\n'
        )
        log_file = tmp_path / "env_trace.log"
        log_file.write_text(log_lines, encoding="utf-8")

        with patch.dict(os.environ, {"ASAP_TRACE_LOG": str(log_file)}):
            result = runner.invoke(app, ["trace", "tr-env"])
        assert result.exit_code == 0
        assert "a -> b" in result.output

    def test_trace_invalid_format(self, tmp_path: Path) -> None:
        """Invalid --format is rejected (line 554)."""
        log_file = tmp_path / "t.log"
        log_file.write_text("{}\n", encoding="utf-8")
        result = runner.invoke(
            app, ["trace", "tr-1", "--log-file", str(log_file), "--format", "xml"]
        )
        assert result.exit_code != 0

    def test_trace_not_found(self, tmp_path: Path) -> None:
        """No matching trace exits with code 1."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("", encoding="utf-8")
        result = runner.invoke(app, ["trace", "nonexistent", "--log-file", str(log_file)])
        assert result.exit_code != 0
