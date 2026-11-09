"""Lock IssueOps skill checks to the manifest skill id."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.process_registration import run

_BODY = """
### Agent name (slug-friendly)
my-agent

### Description
An agent that does research.

### Manifest URL
https://example.com/manifest.json

### HTTP Endpoint
https://example.com/asap

### Skills
{skills}
"""


def _manifest(skill_ids: list[str]) -> dict[str, Any]:
    return {
        "id": "urn:asap:agent:testuser:my-agent",
        "name": "my-agent",
        "version": "1.0.0",
        "description": "An agent that does research.",
        "capabilities": {
            "asap_version": "1.1.0",
            "skills": [{"id": skill_id, "description": "Skill"} for skill_id in skill_ids],
            "state_persistence": False,
            "streaming": False,
            "mcp_tools": [],
        },
        "endpoints": {"asap": "https://example.com/asap"},
    }


_AddrInfo = list[tuple[int, int, int, str, tuple[str, int]]]


def _public_dns(*_args: object, **_kwargs: object) -> _AddrInfo:
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def _http_client(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    client.__exit__.return_value = None
    return client


def _run(tmp_path: Path, *, skills: str, skill_ids: list[str]) -> dict[str, Any]:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("[]")
    output_path = tmp_path / "result.json"
    with (
        patch("scripts.lib.safe_url.socket.getaddrinfo", _public_dns),
        patch(
            "scripts.process_registration.httpx.Client",
            return_value=_http_client(_manifest(skill_ids)),
        ),
    ):
        run(
            body=_BODY.format(skills=skills),
            issue_number="10",
            author="testuser",
            output_path=str(output_path),
            registry_path=str(registry_path),
        )
    assert json.loads(registry_path.read_text()) == []
    loaded: dict[str, Any] = json.loads(output_path.read_text())
    return loaded


def test_registration_rejects_skill_case_neighbor(tmp_path: Path) -> None:
    """``Echo`` does not satisfy a manifest skill id of ``echo``."""
    result = _run(tmp_path, skills="Echo", skill_ids=["echo"])
    assert result["valid"] is False
    assert "Skill 'Echo' not declared in manifest" in result["errors"]


def test_registration_rejects_skill_prefix_neighbor(tmp_path: Path) -> None:
    """``web_research_extra`` does not satisfy ``web_research``."""
    result = _run(tmp_path, skills="web_research_extra", skill_ids=["web_research"])
    assert result["valid"] is False
    assert "Skill 'web_research_extra' not declared in manifest" in result["errors"]
