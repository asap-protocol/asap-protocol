"""Lite Registry filters must match the full id, skill, and hardware token.

A prefix or case-insensitive filter would hand a task to a neighboring agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from asap.discovery.registry import (
    LiteRegistry,
    RegistryEntry,
    find_by_hardware_class,
    find_by_id,
    find_by_inference_mode,
    find_by_io,
    find_by_skill,
)

_EXACT_ID = "urn:asap:agent:bot-01"


def _entry(
    agent_id: str,
    *,
    skill: str,
    hardware_class: str,
    mode: str,
    io_type: str,
) -> RegistryEntry:
    """One registry row whose filter fields are an exact token or a neighbor."""
    return RegistryEntry(
        id=agent_id,
        name=agent_id,
        description="lookup fence",
        endpoints={"http": "https://agent.example/asap"},
        skills=[skill],
        asap_version="1.1.0",
        hardware_class=hardware_class,
        inference_modes=[mode],
        hardware_io=[io_type],
    )


def _registry() -> LiteRegistry:
    """Neighbors are inserted before the exact agent."""
    agents = [
        _entry(
            "urn:asap:agent:bot-010",
            skill="code-review-admin",
            hardware_class="edge-accelerator-xl",
            mode="local-cuda-fast",
            io_type="gpio-ext",
        ),
        _entry(
            "urn:asap:agent:bot-0",
            skill="code",
            hardware_class="edge",
            mode="local",
            io_type="gp",
        ),
        _entry(
            "urn:asap:agent:bot-case",
            skill="Code-Review",
            hardware_class="Edge-Accelerator",
            mode="Local-Cuda",
            io_type="GPIO",
        ),
        _entry(
            "urn:asap:agent:bot-pad",
            skill="code-review ",
            hardware_class="edge-accelerator ",
            mode="local-cuda ",
            io_type="gpio ",
        ),
        _entry(
            _EXACT_ID,
            skill="code-review",
            hardware_class="edge-accelerator",
            mode="local-cuda",
            io_type="gpio",
        ),
    ]
    return LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
        agents=agents,
    )


def _ids(entries: list[RegistryEntry]) -> set[str]:
    """Collect agent URNs from a filter result."""
    return {entry.id for entry in entries}


def test_registry_agent_id_match_is_exact() -> None:
    """``find_by_id`` returns only the URN that matches every character."""
    registry = _registry()
    exact = find_by_id(registry, _EXACT_ID)
    short = find_by_id(registry, "urn:asap:agent:bot-0")
    longer = find_by_id(registry, "urn:asap:agent:bot-010")
    assert exact is not None and exact.id == _EXACT_ID
    assert short is not None and short.id == "urn:asap:agent:bot-0"
    assert longer is not None and longer.id == "urn:asap:agent:bot-010"
    for missing in (
        "urn:asap:agent:bot",
        "urn:asap:agent:bot-01-extra",
        "URN:asap:agent:bot-01",
        "urn:asap:agent:bot-01 ",
        " urn:asap:agent:bot-01",
    ):
        assert find_by_id(registry, missing) is None


def test_registry_skill_match_is_exact() -> None:
    """``find_by_skill`` does not treat a prefix, case, or pad as the skill."""
    registry = _registry()
    assert _ids(find_by_skill(registry, "code-review")) == {_EXACT_ID}
    assert _ids(find_by_skill(registry, "code")) == {"urn:asap:agent:bot-0"}
    assert _ids(find_by_skill(registry, "Code-Review")) == {"urn:asap:agent:bot-case"}
    assert _ids(find_by_skill(registry, "code-review ")) == {"urn:asap:agent:bot-pad"}
    assert find_by_skill(registry, "code-review-extra") == []


def test_registry_hardware_filters_match_exactly() -> None:
    """Hardware class, inference mode, and I/O use the full token."""
    registry = _registry()
    assert _ids(find_by_hardware_class(registry, "edge-accelerator")) == {_EXACT_ID}
    assert _ids(find_by_hardware_class(registry, "edge")) == {"urn:asap:agent:bot-0"}
    assert _ids(find_by_inference_mode(registry, "local-cuda")) == {_EXACT_ID}
    assert _ids(find_by_inference_mode(registry, "local")) == {"urn:asap:agent:bot-0"}
    assert _ids(find_by_io(registry, "gpio")) == {_EXACT_ID}
    assert _ids(find_by_io(registry, "gp")) == {"urn:asap:agent:bot-0"}
