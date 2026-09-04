"""Tests for scripts/check_auto_registration_merge_eligible.py add-only policy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asap.discovery.registry import LiteRegistry, RegistryEntry
from asap.models.entities import VerificationStatus
from asap.models.enums import VerificationState

from scripts.check_auto_registration_merge_eligible import evaluate


def _entry(
    agent_id: str,
    *,
    http: str = "https://example.com/asap",
    verified: bool = False,
) -> RegistryEntry:
    status = VerificationState.VERIFIED if verified else VerificationState.PENDING
    return RegistryEntry(
        id=agent_id,
        name=agent_id.rsplit(":", 1)[-1],
        description="d",
        endpoints={"http": http, "manifest": "https://example.com/m"},
        skills=["echo"],
        asap_version="2.2.0",
        verification=VerificationStatus(status=status),
    )


def _write_registry(path: Path, agents: list[RegistryEntry]) -> None:
    registry = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        agents=agents,
    )
    path.write_text(registry.model_dump_json(indent=2), encoding="utf-8")


def test_evaluate_allows_new_pending_agent(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    existing = _entry("urn:asap:agent:acme:bot")
    _write_registry(base, [existing])
    _write_registry(head, [existing, _entry("urn:asap:agent:new:bot")])
    ok, message = evaluate(base, head)
    assert ok is True
    assert "add-only" in message


def test_evaluate_rejects_overwrite_of_existing_id(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    victim = _entry("urn:asap:agent:acme:bot", http="https://acme.example/asap")
    hijack = _entry("urn:asap:agent:acme:bot", http="https://evil.example/asap")
    _write_registry(base, [victim])
    _write_registry(head, [hijack])
    ok, message = evaluate(base, head)
    assert ok is False
    assert "already registered" in message
    assert "urn:asap:agent:acme:bot" in message


def test_evaluate_rejects_verified_demotion_overwrite(tmp_path: Path) -> None:
    """Verified listings must not be replaced with pending attacker endpoints."""
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    victim = _entry("urn:asap:agent:acme:bot", verified=True)
    hijack = _entry("urn:asap:agent:acme:bot", http="https://evil.example/asap")
    _write_registry(base, [victim])
    _write_registry(head, [hijack])
    ok, message = evaluate(base, head)
    assert ok is False
    assert "already registered" in message


def test_evaluate_rejects_deletion_of_existing_id(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    keep = _entry("urn:asap:agent:keep:bot")
    drop = _entry("urn:asap:agent:drop:bot")
    _write_registry(base, [keep, drop])
    _write_registry(head, [keep])
    ok, message = evaluate(base, head)
    assert ok is False
    assert "cannot remove" in message
    assert "urn:asap:agent:drop:bot" in message


def test_evaluate_rejects_new_verified_agent(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    _write_registry(base, [])
    _write_registry(head, [_entry("urn:asap:agent:new:bot", verified=True)])
    ok, message = evaluate(base, head)
    assert ok is False
    assert "verification.status=verified" in message


def test_evaluate_invalid_json_is_ineligible(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    _write_registry(base, [])
    head.write_text("{not json", encoding="utf-8")
    ok, message = evaluate(base, head)
    assert ok is False
    assert "Failed to parse" in message


def test_evaluate_array_form_overwrite_is_ineligible(tmp_path: Path) -> None:
    """CI also accepts the agents-array registry shape."""
    victim: dict[str, Any] = _entry("urn:asap:agent:acme:bot").model_dump(mode="json")
    hijack: dict[str, Any] = _entry(
        "urn:asap:agent:acme:bot", http="https://evil.example/asap"
    ).model_dump(mode="json")
    base = tmp_path / "base.json"
    head = tmp_path / "head.json"
    base.write_text(json.dumps([victim]), encoding="utf-8")
    head.write_text(json.dumps([hijack]), encoding="utf-8")
    ok, message = evaluate(base, head)
    assert ok is False
    assert "already registered" in message
