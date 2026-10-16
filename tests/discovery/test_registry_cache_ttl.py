"""Lock Lite Registry client cache expiry at the exact TTL instant."""

from __future__ import annotations

import json

import httpx
import pytest

from asap.discovery.registry import discover_from_registry, reset_registry_cache

_FROZEN_START = 1_000.0
_TTL_SECONDS = 60
_REGISTRY_URL = "https://ttl-fence.example/registry.json"


def _registry_body(agent_name: str, updated_at: str) -> str:
    payload = {
        "version": "1.0",
        "updated_at": updated_at,
        "agents": [
            {
                "id": "urn:asap:agent:alpha",
                "name": agent_name,
                "description": "Cache TTL fence agent",
                "endpoints": {"http": "https://alpha.example.com/asap"},
                "skills": ["code_review"],
                "asap_version": "1.1.0",
            }
        ],
    }
    return json.dumps(payload)


async def test_registry_cache_hits_until_exact_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache is reused one second before TTL and refetched when now equals expiry."""
    clock = {"now": _FROZEN_START}
    monkeypatch.setattr("asap.discovery.registry.time.monotonic", lambda: clock["now"])
    bodies = [
        _registry_body("Cached Agent", "2026-02-07T00:00:00Z"),
        _registry_body("Refreshed Agent", "2026-02-07T00:01:00Z"),
    ]
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies[calls["count"]]
        calls["count"] += 1
        assert str(request.url) == _REGISTRY_URL
        return httpx.Response(status_code=200, content=body.encode())

    transport = httpx.MockTransport(handler)
    reset_registry_cache()
    try:
        cached = await discover_from_registry(
            registry_url=_REGISTRY_URL,
            ttl_seconds=_TTL_SECONDS,
            transport=transport,
        )
        assert cached.agents[0].name == "Cached Agent"
        assert calls["count"] == 1

        clock["now"] = _FROZEN_START + _TTL_SECONDS - 1
        still_cached = await discover_from_registry(
            registry_url=_REGISTRY_URL,
            ttl_seconds=_TTL_SECONDS,
            transport=transport,
        )
        assert still_cached.agents[0].name == "Cached Agent"
        assert calls["count"] == 1

        clock["now"] = _FROZEN_START + _TTL_SECONDS
        refreshed = await discover_from_registry(
            registry_url=_REGISTRY_URL,
            ttl_seconds=_TTL_SECONDS,
            transport=transport,
        )
        assert refreshed.agents[0].name == "Refreshed Agent"
        assert calls["count"] == 2
    finally:
        reset_registry_cache()
