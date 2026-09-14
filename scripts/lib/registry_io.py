"""Shared I/O utilities for registry scripts.

Provides input sanitization, validation-result writing, and atomic
registry file load/save used by both registration and removal workflows.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

# Canonical Lite Registry schema version written by IssueOps (matches registry.json).
_DEFAULT_REGISTRY_VERSION = "1.0"


def sanitize_input(text: str, max_length: int = 1000) -> str:
    """Strip code blocks, HTML, and control chars; clamp length."""
    if not text:
        return ""
    clean = re.sub(r"```[\s\S]*?```", "", text)
    clean = re.sub(r"`[^`]*`", "", clean)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", clean)
    return clean.strip()[:max_length]


def write_validation_result(
    output_path: str,
    *,
    valid: bool = False,
    errors: str = "",
    debug_id: str | None = None,
) -> None:
    out: dict[str, bool | str] = {"valid": valid, "errors": errors}
    if debug_id:
        out["debug_id"] = debug_id
    Path(output_path).write_text(json.dumps(out))


def load_registry(path: str) -> list[dict[str, Any]]:
    """Load registry JSON (array or LiteRegistry wrapper). Returns [] if missing."""
    p = Path(path)
    if not p.exists():
        return []
    raw: object = json.loads(p.read_text())
    if isinstance(raw, list):
        return cast(list[dict[str, Any]], raw)
    if isinstance(raw, dict) and "agents" in raw:
        return cast(list[dict[str, Any]], raw["agents"])
    return []


def _registry_version_from_existing(path: Path) -> str:
    """Preserve ``version`` from an existing LiteRegistry object file when present."""
    if not path.exists():
        return _DEFAULT_REGISTRY_VERSION
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _DEFAULT_REGISTRY_VERSION
    if isinstance(raw, dict):
        version = raw.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return _DEFAULT_REGISTRY_VERSION


def _utc_now_iso_z() -> str:
    """UTC timestamp with ``Z`` suffix (same shape as production registry.json)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def save_registry(path: str, agents: list[dict[str, Any]]) -> None:
    """Atomically write a LiteRegistry object ``{version, updated_at, agents}``.

    IssueOps previously dumped a bare agents array. That demoted production
    ``registry.json`` (object form) and broke Python
    ``LiteRegistry.model_validate_json`` / ``discover_from_registry`` while CI
    ``validate_registry.py`` still accepted the array. Always persist the object
    envelope so registration/removal cannot corrupt the discovery document.
    """
    target = Path(path)
    payload: dict[str, Any] = {
        "version": _registry_version_from_existing(target),
        "updated_at": _utc_now_iso_z(),
        "agents": agents,
    }
    content = json.dumps(payload, indent=2) + "\n"
    temp_dir = target.parent if target.parent != Path() else Path.cwd()
    fd, tmp = tempfile.mkstemp(dir=temp_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_revoked(path: str) -> dict[str, Any]:
    """Load revoked_agents.json; returns default if missing or malformed."""
    p = Path(path)
    if not p.exists():
        return {"revoked": [], "version": "1.0"}
    raw: object = json.loads(p.read_text())
    if isinstance(raw, dict) and "revoked" in raw:
        return cast(dict[str, Any], raw)
    return {"revoked": [], "version": "1.0"}


def save_revoked(path: str, data: dict[str, Any]) -> None:
    """Write revoked_agents.json atomically (temp + rename)."""
    target = Path(path)
    content = json.dumps(data, indent=2) + "\n"
    temp_dir = target.parent if target.parent != Path() else Path.cwd()
    fd, tmp = tempfile.mkstemp(dir=temp_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
