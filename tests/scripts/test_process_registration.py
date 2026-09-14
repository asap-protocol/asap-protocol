"""Tests for scripts/process_registration.py (IssueOps)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from asap.crypto.keys import generate_keypair
from asap.crypto.signing import sign_manifest
from asap.discovery.registry import LiteRegistry
from asap.models.entities import Capability, Endpoint, Manifest, Skill

from scripts.process_registration import (
    fetch_manifest,
    parse_issue_body,
    run,
)
from lib.registry_io import load_registry, save_registry


def _registry_agents(path: Path) -> list[object]:
    """Return agents list from array or LiteRegistry object file."""
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return raw
    assert isinstance(raw, dict) and "agents" in raw
    return raw["agents"]


def _fake_getaddrinfo_public(
    host: str,
    port: object,
    family: int = 0,
    sock_type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Return a public IP for any hostname (avoids DNS in tests)."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


# Sample GitHub Issue Form body (markdown with ### headers from register_agent.yml)
VALID_BODY_MINIMAL = """
### Agent name (slug-friendly)
my-agent

### Description
An agent that does research.

### Manifest URL
https://example.com/manifest.json

### HTTP Endpoint
https://example.com/asap

### WebSocket Endpoint (optional)


### Skills
web_research, summarization

### Built with (framework)


### Repository URL (optional)


### Documentation URL (optional)


### Confirmation
- [x] I confirm
"""

VALID_BODY_WITH_OPTIONALS = """
### Agent name (slug-friendly)
other-agent

### Description
Another agent.

### Manifest URL
https://api.example.com/.well-known/asap-manifest.json

### HTTP Endpoint
https://api.example.com/asap

### WebSocket Endpoint (optional)
wss://api.example.com/asap/events

### Skills
code_review

### Built with (framework)
LangChain

### Repository URL (optional)
https://github.com/me/repo

### Documentation URL (optional)
https://docs.example.com/agent

### Confirmation
- [x] I confirm
"""

# Minimal valid Manifest JSON (matches expected_id for author "testuser", name "my-agent")
VALID_MANIFEST_JSON = {
    "id": "urn:asap:agent:testuser:my-agent",
    "name": "my-agent",
    "version": "1.0.0",
    "description": "An agent that does research.",
    "capabilities": {
        "asap_version": "1.1.0",
        "skills": [
            {"id": "web_research", "description": "Research"},
            {"id": "summarization", "description": "Summarize"},
        ],
        "state_persistence": False,
        "streaming": False,
        "mcp_tools": [],
    },
    "endpoints": {
        "asap": "https://example.com/asap",
        "events": None,
    },
}


class TestParseIssueBody:
    """Tests for parse_issue_body."""

    def test_parses_valid_minimal_body(self) -> None:
        out = parse_issue_body(VALID_BODY_MINIMAL)
        assert out["name"] == "my-agent"
        assert out["description"] == "An agent that does research."
        assert out["manifest_url"] == "https://example.com/manifest.json"
        assert out["http_endpoint"] == "https://example.com/asap"
        assert out["skills"] == "web_research, summarization"
        assert out.get("websocket_endpoint") == ""
        assert out.get("built_with") == ""

    def test_parses_valid_body_with_optionals(self) -> None:
        out = parse_issue_body(VALID_BODY_WITH_OPTIONALS)
        assert out["name"] == "other-agent"
        assert out["repository_url"] == "https://github.com/me/repo"
        assert out["documentation_url"] == "https://docs.example.com/agent"
        assert out["built_with"] == "LangChain"
        assert out["websocket_endpoint"] == "wss://api.example.com/asap/events"

    def test_parses_empty_body(self) -> None:
        assert parse_issue_body("") == {}
        assert parse_issue_body("   \n  ") == {}

    def test_parses_invalid_markdown_gracefully(self) -> None:
        """Non-form markdown returns only matched fields."""
        body = "### Agent name (slug-friendly)\nfoo\n### Unknown section\nbar"
        out = parse_issue_body(body)
        assert out.get("name") == "foo"
        assert "Unknown section" not in out

    def test_parse_body_with_category_and_tags(self) -> None:
        """parse_issue_body extracts Category and Tags."""
        body = "### Category\n\nCoding\n\n### Tags\n\nai, code_review, testing"
        parsed = parse_issue_body(body)
        assert parsed["category"] == "Coding"
        assert parsed["tags"] == "ai, code_review, testing"


class TestFetchManifestSSRF:
    """Tests for fetch_manifest SSRF protection (RF-1)."""

    def test_blocks_metadata_url(self) -> None:
        """Block cloud metadata endpoints."""
        with pytest.raises(ValueError, match="Blocked URL"):
            fetch_manifest("http://169.254.169.254/latest/meta-data/")

    def test_blocks_localhost(self) -> None:
        """Block loopback addresses."""
        with pytest.raises(ValueError, match="Blocked URL"):
            fetch_manifest("http://localhost/manifest.json")

    def test_blocks_private_ip(self) -> None:
        """Block private IP ranges."""
        with pytest.raises(ValueError, match="Blocked URL"):
            fetch_manifest("http://192.168.1.1/manifest.json")

    def test_rejects_http_redirect_without_following(self) -> None:
        """302 redirect to metadata must fail without following (SSRF parity)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Redirect",
            request=MagicMock(),
            response=mock_resp,
        )
        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_resp
        mock_client.__exit__.return_value = None
        with (
            patch("scripts.process_registration.httpx.Client", return_value=mock_client),
            pytest.raises(httpx.HTTPStatusError),
        ):
            fetch_manifest("https://example.com/manifest.json")

    def test_accepts_valid_signed_envelope(self) -> None:
        """Signed manifest envelope unwraps to inner Manifest (compliance harness parity)."""
        manifest = Manifest(
            id="urn:asap:agent:testuser:my-agent",
            name="my-agent",
            version="1.0.0",
            description="Signed envelope test",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[
                    Skill(id="web_research", description="Research"),
                    Skill(id="summarization", description="Summarize"),
                ],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        private_key, _ = generate_keypair()
        signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(signed_payload),
        ):
            result = fetch_manifest("https://example.com/manifest.json")
        assert result.id == "urn:asap:agent:testuser:my-agent"
        assert result.name == "my-agent"

    def test_rejects_tampered_signed_envelope(self) -> None:
        """Tampered signed manifest fails signature verification."""
        manifest = Manifest(
            id="urn:asap:agent:testuser:my-agent",
            name="my-agent",
            version="1.0.0",
            description="Tampered envelope test",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="web_research", description="Research")],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        private_key, _ = generate_keypair()
        signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
        inner = signed_payload["manifest"]
        assert isinstance(inner, dict)
        inner["name"] = "tampered-name"
        with (
            patch(
                "scripts.process_registration.httpx.Client",
                return_value=_mock_httpx_client(signed_payload),
            ),
            pytest.raises(Exception, match="signature|verification|Invalid"),
        ):
            fetch_manifest("https://example.com/manifest.json")


def _mock_httpx_client(response_json: dict) -> MagicMock:
    """Build a MagicMock for httpx.Client that returns the given JSON as manifest."""
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(response_json)
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_resp
    mock_client.__exit__.return_value = None
    return mock_client


class TestProcessRegistrationRun:
    @pytest.fixture(autouse=True)
    def _patch_dns(self) -> None:
        """Mock getaddrinfo so test URLs resolve to public IP."""
        with patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_getaddrinfo_public):
            yield

    def test_valid_issue_writes_registry_and_valid_result(
        self,
        tmp_path: Path,
    ) -> None:
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(VALID_MANIFEST_JSON),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"

            run(
                body=VALID_BODY_MINIMAL,
                issue_number="1",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )

        result = json.loads(output_path.read_text())
        assert result["valid"] is True

        registry = _registry_agents(registry_path)
        assert len(registry) == 1
        entry = registry[0]
        assert entry["id"] == "urn:asap:agent:testuser:my-agent"
        assert entry["name"] == "my-agent"
        assert entry["skills"] == ["web_research", "summarization"]
        assert "http" in entry["endpoints"]
        assert entry["endpoints"]["manifest"] == "https://example.com/manifest.json"

    def test_valid_issue_with_optionals_passes_through(
        self,
        tmp_path: Path,
    ) -> None:
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["id"] = "urn:asap:agent:testuser:other-agent"
        manifest["name"] = "other-agent"
        manifest["capabilities"] = dict(manifest["capabilities"])
        manifest["capabilities"]["skills"] = [{"id": "code_review", "description": "Review"}]
        manifest["endpoints"] = dict(manifest["endpoints"])
        manifest["endpoints"]["asap"] = "https://api.example.com/asap"
        manifest["endpoints"]["events"] = "wss://api.example.com/asap/events"

        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(manifest),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"

            run(
                body=VALID_BODY_WITH_OPTIONALS,
                issue_number="2",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )

        result = json.loads(output_path.read_text())
        assert result["valid"] is True
        registry = _registry_agents(registry_path)
        entry = registry[0]
        assert entry.get("repository_url") == "https://github.com/me/repo"
        assert entry.get("documentation_url") == "https://docs.example.com/agent"
        assert entry.get("built_with") == "LangChain"

    def test_valid_issue_with_category_tags_writes_registry_entry(
        self,
        tmp_path: Path,
    ) -> None:
        """Body with Category and Tags produces registry entry with category and tags."""
        body_with_category_tags = (
            VALID_BODY_MINIMAL + "\n\n### Category\n\nCoding\n\n### Tags\n\nai, code_review"
        )
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(VALID_MANIFEST_JSON),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"
            run(
                body=body_with_category_tags,
                issue_number="8",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )
        result = json.loads(output_path.read_text())
        assert result["valid"] is True
        registry = _registry_agents(registry_path)
        entry = registry[0]
        assert entry.get("category") == "Coding"
        assert entry.get("tags") == ["ai", "code_review"]

    def test_valid_issue_derives_hardware_fields_from_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        """Manifest with hardware/inference writes derived registry fields."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["capabilities"] = dict(manifest["capabilities"])
        manifest["capabilities"]["hardware"] = {
            "class": "edge_accelerator",
            "model": "jetson_orin_nano_super_8gb",
            "io": ["gpio", "i2c"],
        }
        manifest["capabilities"]["inference"] = {
            "modes": ["cloud", "local_cuda"],
            "local_models": [
                {"id": "Phi-3-mini-4k-instruct-Q4_K_M", "quantization": "Q4_K_M"},
            ],
        }
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(manifest),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"
            run(
                body=VALID_BODY_MINIMAL,
                issue_number="9",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )
        result = json.loads(output_path.read_text())
        assert result["valid"] is True
        entry = _registry_agents(registry_path)[0]
        assert entry.get("hardware_class") == "edge_accelerator"
        assert entry.get("inference_modes") == ["cloud", "local_cuda"]
        assert entry.get("hardware_io") == ["gpio", "i2c"]

    def test_valid_issue_accepts_signed_manifest_envelope(
        self,
        tmp_path: Path,
    ) -> None:
        """IssueOps accepts signed manifest envelopes (parity with compliance harness)."""
        manifest = Manifest(
            id="urn:asap:agent:testuser:my-agent",
            name="my-agent",
            version="1.0.0",
            description="An agent that does research.",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[
                    Skill(id="web_research", description="Research"),
                    Skill(id="summarization", description="Summarize"),
                ],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        private_key, _ = generate_keypair()
        signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(signed_payload),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"
            run(
                body=VALID_BODY_MINIMAL,
                issue_number="11",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )
        result = json.loads(output_path.read_text())
        assert result["valid"] is True
        entry = _registry_agents(registry_path)[0]
        assert entry["id"] == "urn:asap:agent:testuser:my-agent"

    def test_invalid_tampered_signed_manifest_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        """Tampered signed manifest envelope fails before registry write."""
        manifest = Manifest(
            id="urn:asap:agent:testuser:my-agent",
            name="my-agent",
            version="1.0.0",
            description="An agent that does research.",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[
                    Skill(id="web_research", description="Research"),
                    Skill(id="summarization", description="Summarize"),
                ],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        private_key, _ = generate_keypair()
        signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
        inner = signed_payload["manifest"]
        assert isinstance(inner, dict)
        inner["name"] = "wrong-slug"
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(signed_payload),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"
            run(
                body=VALID_BODY_MINIMAL,
                issue_number="12",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )
        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert (
            "schema validation" in result["errors"].lower()
            or "signature" in result["errors"].lower()
        )
        assert json.loads(registry_path.read_text()) == []

    def test_invalid_missing_required_fields(self, tmp_path: Path) -> None:
        output_path = tmp_path / "result.json"
        run(
            body="### Agent name (slug-friendly)\n\n### Description\n\n### Manifest URL\n\n### HTTP Endpoint\n\n### Skills\n",
            issue_number="3",
            author="user",
            output_path=str(output_path),
            registry_path=str(tmp_path / "registry.json"),
        )
        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "errors" in result
        assert "Missing" in result["errors"] or "required" in result["errors"].lower()
        assert "debug_id" in result
        assert result["debug_id"].startswith("ASAP-")

    def test_invalid_manifest_id_mismatch(
        self,
        tmp_path: Path,
    ) -> None:
        manifest_wrong_id = dict(VALID_MANIFEST_JSON)
        manifest_wrong_id["id"] = "urn:asap:agent:other:my-agent"
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(manifest_wrong_id),
        ):
            registry_path = tmp_path / "registry.json"
            registry_path.write_text("[]")
            output_path = tmp_path / "result.json"

            run(
                body=VALID_BODY_MINIMAL,
                issue_number="4",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert (
            "Manifest id must be" in result["errors"]
            or "urn:asap:agent:testuser" in result["errors"]
        )

    def test_invalid_manifest_unreachable(
        self,
        tmp_path: Path,
    ) -> None:
        import httpx

        with patch(
            "scripts.process_registration.httpx.Client",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            output_path = tmp_path / "result.json"
            run(
                body=VALID_BODY_MINIMAL,
                issue_number="5",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(tmp_path / "registry.json"),
            )
        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "unreachable" in result["errors"].lower() or "error" in result["errors"].lower()

    def test_invalid_duplicate_agent_id(
        self,
        tmp_path: Path,
    ) -> None:
        existing = [
            {
                "id": "urn:asap:agent:testuser:my-agent",
                "name": "my-agent",
                "description": "Existing",
                "endpoints": {
                    "http": "https://example.com/asap",
                    "manifest": "https://example.com/m.json",
                },
                "skills": ["web_research"],
                "asap_version": "1.1.0",
            }
        ]
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(json.dumps(existing))
        output_path = tmp_path / "result.json"

        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(VALID_MANIFEST_JSON),
        ):
            run(
                body=VALID_BODY_MINIMAL,
                issue_number="6",
                author="testuser",
                output_path=str(output_path),
                registry_path=str(registry_path),
            )

        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "already registered" in result["errors"].lower()
        # Registry unchanged
        assert json.loads(registry_path.read_text()) == existing

    def test_blocks_ssrf_manifest_url(
        self,
        tmp_path: Path,
    ) -> None:
        """Blocked manifest URL (SSRF) returns validation error (RF-1)."""
        output_path = tmp_path / "result.json"
        body = VALID_BODY_MINIMAL.replace(
            "https://example.com/manifest.json",
            "http://169.254.169.254/latest/meta-data/",
        )
        run(
            body=body,
            issue_number="7",
            author="testuser",
            output_path=str(output_path),
            registry_path=str(tmp_path / "registry.json"),
        )
        result = json.loads(output_path.read_text())
        assert result["valid"] is False
        assert "Blocked" in result["errors"] or "private" in result["errors"].lower()

    def _run_with_manifest(
        self,
        tmp_path: Path,
        manifest: dict,
        *,
        body: str = VALID_BODY_MINIMAL,
        author: str = "testuser",
        issue_number: str = "10",
    ) -> tuple[dict, Path]:
        registry_path = tmp_path / "registry.json"
        registry_path.write_text("[]")
        output_path = tmp_path / "result.json"
        with patch(
            "scripts.process_registration.httpx.Client",
            return_value=_mock_httpx_client(manifest),
        ):
            run(
                body=body,
                issue_number=issue_number,
                author=author,
                output_path=str(output_path),
                registry_path=str(registry_path),
            )
        return json.loads(output_path.read_text()), registry_path

    def test_invalid_manifest_name_mismatch(self, tmp_path: Path) -> None:
        """Manifest name must match issue slug."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["name"] = "wrong-slug"
        result, registry_path = self._run_with_manifest(tmp_path, manifest)
        assert result["valid"] is False
        assert "Manifest name must match" in result["errors"]
        assert json.loads(registry_path.read_text()) == []

    def test_invalid_undeclared_skill(self, tmp_path: Path) -> None:
        """Issue skills must be declared in manifest capabilities."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["capabilities"] = dict(manifest["capabilities"])
        manifest["capabilities"]["skills"] = [
            {"id": "web_research", "description": "Research"},
        ]
        result, registry_path = self._run_with_manifest(tmp_path, manifest)
        assert result["valid"] is False
        assert "summarization" in result["errors"]
        assert "not declared in manifest" in result["errors"]
        assert json.loads(registry_path.read_text()) == []

    def test_invalid_http_endpoint_mismatch(self, tmp_path: Path) -> None:
        """Issue HTTP endpoint must match manifest endpoints.asap."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["endpoints"] = dict(manifest["endpoints"])
        manifest["endpoints"]["asap"] = "https://other.example/asap"
        result, registry_path = self._run_with_manifest(tmp_path, manifest)
        assert result["valid"] is False
        assert "HTTP endpoint must match manifest" in result["errors"]
        assert json.loads(registry_path.read_text()) == []

    def test_invalid_websocket_endpoint_mismatch(self, tmp_path: Path) -> None:
        """Optional issue WebSocket endpoint must match manifest endpoints.events."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["id"] = "urn:asap:agent:testuser:other-agent"
        manifest["name"] = "other-agent"
        manifest["capabilities"] = dict(manifest["capabilities"])
        manifest["capabilities"]["skills"] = [{"id": "code_review", "description": "Review"}]
        manifest["endpoints"] = {
            "asap": "https://api.example.com/asap",
            "events": "wss://api.example.com/wrong/events",
        }
        result, registry_path = self._run_with_manifest(
            tmp_path,
            manifest,
            body=VALID_BODY_WITH_OPTIONALS,
        )
        assert result["valid"] is False
        assert "WebSocket endpoint must match manifest" in result["errors"]
        assert json.loads(registry_path.read_text()) == []

    def test_author_case_normalized_for_expected_id(self, tmp_path: Path) -> None:
        """GitHub author is lowercased when building expected manifest URN."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["id"] = "urn:asap:agent:testuser:my-agent"
        result, registry_path = self._run_with_manifest(
            tmp_path,
            manifest,
            author="TestUser",
        )
        assert result["valid"] is True
        assert _registry_agents(registry_path)[0]["id"] == "urn:asap:agent:testuser:my-agent"

    def test_invalid_manifest_schema_validation(self, tmp_path: Path) -> None:
        """Malformed manifest JSON fails closed before registry write."""
        incomplete_manifest = {"id": "urn:asap:agent:testuser:my-agent"}
        result, registry_path = self._run_with_manifest(tmp_path, incomplete_manifest)
        assert result["valid"] is False
        assert "Manifest failed schema validation" in result["errors"]
        assert json.loads(registry_path.read_text()) == []

    def test_invalid_manifest_hardware_io_enum(self, tmp_path: Path) -> None:
        """Invalid v2.4 hardware.io enum fails before registry write."""
        manifest = dict(VALID_MANIFEST_JSON)
        manifest["capabilities"] = dict(manifest["capabilities"])
        manifest["capabilities"]["hardware"] = {"class": "edge_accelerator", "io": ["wifi"]}
        result, registry_path = self._run_with_manifest(tmp_path, manifest)
        assert result["valid"] is False
        assert "Manifest failed schema validation" in result["errors"]
        assert json.loads(registry_path.read_text()) == []


class TestLoadRegistry:
    """Tests for load_registry."""

    def test_load_empty_missing_file(self, tmp_path: Path) -> None:
        """Missing file returns empty list."""
        assert load_registry(str(tmp_path / "nonexistent.json")) == []

    def test_load_malformed_json_raises(self, tmp_path: Path) -> None:
        """Malformed JSON raises json.JSONDecodeError."""
        bad = tmp_path / "bad.json"
        bad.write_text("{ invalid")
        with pytest.raises(json.JSONDecodeError):
            load_registry(str(bad))

    def test_load_lite_registry_wrapper_format(self, tmp_path: Path) -> None:
        """LiteRegistry wrapper format (agents key) is supported."""
        data = {
            "version": "1.0",
            "agents": [{"id": "urn:asap:agent:test", "name": "Test"}],
        }
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(data))
        result = load_registry(str(path))
        assert len(result) == 1
        assert result[0]["id"] == "urn:asap:agent:test"

    def test_load_array_format(self, tmp_path: Path) -> None:
        """Array format (direct list) is supported."""
        data = [{"id": "urn:asap:agent:one", "name": "One"}]
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(data))
        result = load_registry(str(path))
        assert len(result) == 1
        assert result[0]["id"] == "urn:asap:agent:one"


class TestSaveRegistry:
    """Tests for save_registry atomic write."""

    def test_save_registry_atomic(self, tmp_path: Path) -> None:
        """save_registry writes atomically as a LiteRegistry object."""
        path = tmp_path / "registry.json"
        agents = [
            {
                "id": "urn:asap:agent:test",
                "name": "Test",
                "description": "Test agent",
                "endpoints": {"http": "https://example.com/asap"},
                "skills": ["skill1"],
                "asap_version": "1.1.0",
            }
        ]
        save_registry(str(path), agents)
        raw = json.loads(path.read_text())
        assert isinstance(raw, dict)
        assert raw["version"] == "1.0"
        assert isinstance(raw["updated_at"], str) and raw["updated_at"].endswith("Z")
        assert raw["agents"] == agents
        # discover_from_registry uses LiteRegistry.model_validate_json; a root
        # array raises ValidationError even when agents themselves are valid.
        parsed = LiteRegistry.model_validate(raw)
        assert parsed.version == "1.0"
        assert len(parsed.agents) == 1
        assert str(parsed.agents[0].id) == "urn:asap:agent:test"

    def test_save_registry_preserves_existing_version(self, tmp_path: Path) -> None:
        """save_registry keeps version from an existing LiteRegistry file."""
        path = tmp_path / "registry.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.1",
                    "updated_at": "2020-01-01T00:00:00Z",
                    "agents": [],
                }
            )
        )
        agents = [{"id": "urn:asap:agent:a:b", "name": "B"}]
        save_registry(str(path), agents)
        raw = json.loads(path.read_text())
        assert raw["version"] == "1.1"
        assert raw["agents"] == agents
        assert raw["updated_at"] != "2020-01-01T00:00:00Z"

    def test_save_registry_upgrades_bare_array_to_object(self, tmp_path: Path) -> None:
        """IssueOps must not leave production registry as a bare agents array."""
        path = tmp_path / "registry.json"
        path.write_text("[]")
        agents = [{"id": "urn:asap:agent:a:b", "name": "B"}]
        save_registry(str(path), agents)
        raw = json.loads(path.read_text())
        assert set(raw) >= {"version", "updated_at", "agents"}
        assert raw["agents"] == agents
