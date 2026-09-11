"""Unit tests for agent_routes registration helpers (capability parsing and grants)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from asap.auth.capabilities import CapabilityDefinition, CapabilityRegistry
from asap.transport.agent_routes import (
    apply_capability_specs_to_registry,
    parse_capability_registration_body,
)


class TestParseCapabilityRegistrationBody:
    """parse_capability_registration_body edge cases for register JSON bodies."""

    def test_missing_capabilities_returns_empty(self) -> None:
        names, specs = parse_capability_registration_body({})
        assert names == []
        assert specs == []

    def test_non_list_capabilities_returns_empty(self) -> None:
        names, specs = parse_capability_registration_body({"capabilities": "file:read"})
        assert names == []
        assert specs == []

    def test_string_capabilities_trimmed(self) -> None:
        names, specs = parse_capability_registration_body(
            {"capabilities": ["file:read", "  ", "file:write"]}
        )
        assert names == ["file:read", "file:write"]
        assert specs == [{"name": "file:read"}, {"name": "file:write"}]

    def test_dict_capabilities_preserve_constraints(self) -> None:
        body: dict[str, Any] = {
            "capabilities": [
                {"name": "file:read", "constraints": {"path": "/tmp"}},
                {"name": 42},
                {"name": ""},
                {"constraints": {"path": "/etc"}},
            ]
        }
        names, specs = parse_capability_registration_body(body)
        assert names == ["file:read"]
        assert specs == [{"name": "file:read", "constraints": {"path": "/tmp"}}]


class TestApplyCapabilitySpecsToRegistry:
    """apply_capability_specs_to_registry mirrors register-time capability grants."""

    @pytest.fixture()
    def registry(self) -> CapabilityRegistry:
        reg = CapabilityRegistry()
        reg.register(CapabilityDefinition(name="file:read", description="Read files"))
        return reg

    def test_grants_known_capability(self, registry: CapabilityRegistry) -> None:
        grants = apply_capability_specs_to_registry(
            registry,
            "agent-1",
            "host-1",
            [{"name": "file:read", "constraints": {"path": "/tmp"}}],
        )
        assert len(grants) == 1
        assert grants[0]["capability"] == "file:read"
        assert grants[0]["status"] == "active"

    def test_denies_unknown_capability(self, registry: CapabilityRegistry) -> None:
        grants = apply_capability_specs_to_registry(
            registry,
            "agent-1",
            "host-1",
            [{"name": "unknown:cap"}],
        )
        assert len(grants) == 1
        assert grants[0]["status"] == "denied"
        assert "not found" in grants[0]["reason"]

    def test_skips_specs_without_name(self, registry: CapabilityRegistry) -> None:
        grants = apply_capability_specs_to_registry(
            registry,
            "agent-1",
            "host-1",
            [{"constraints": {"path": "/tmp"}}, {"name": 99}],
        )
        assert grants == []

    def test_skips_identical_active_grant_write(self, registry: CapabilityRegistry) -> None:
        constraints: dict[str, Any] = {"path": {"in": ["/tmp"]}}
        first = registry.grant(
            "agent-1",
            "file:read",
            granted_by="host-1",
            constraints=constraints,
        )
        grants = apply_capability_specs_to_registry(
            registry,
            "agent-1",
            "host-1",
            [{"name": "file:read", "constraints": constraints}],
        )
        assert grants == [{"capability": "file:read", "status": "active"}]
        current = registry.get_grants("agent-1")[0]
        assert current is first

    def test_rewrites_when_existing_grant_has_expiry(self, registry: CapabilityRegistry) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        registry.grant(
            "agent-1",
            "file:read",
            granted_by="host-1",
            expires_at=expires,
        )
        apply_capability_specs_to_registry(
            registry,
            "agent-1",
            "host-1",
            [{"name": "file:read"}],
        )
        assert registry.get_grants("agent-1")[0].expires_at is None
