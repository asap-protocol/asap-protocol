"""The ASAP endpoints that a card names are checked before they reach a manifest.

Whatever later calls ``manifest.endpoints.asap`` makes that request from its
own network, so a card must not point it at a metadata address, a loopback
admin URL, or a local file. The pure mapping requires ``https`` (``wss`` for
the events endpoint), a host, and no user info; ingest also refuses an IP
literal that is not public and ``localhost``, unless ``allow_private_hosts``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from asap.adapters.a2a import (
    A2AMappingError,
    A2ATrustConfig,
    AgentCard,
    AgentCardIngestor,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from asap.models.entities import Endpoint
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_CARD_URL = "https://agent.example.com/.well-known/agent-card.json"


def _card_json(asap: str, events: str | None = None) -> dict[str, Any]:
    manifest = research_manifest(endpoints=Endpoint(asap="https://agent.example.com/asap"))
    card_json = manifest_to_agent_card(manifest, research_card_inputs()).card.model_dump(
        mode="json"
    )
    params = card_json["capabilities"]["extensions"][0]["params"]
    params["asap_endpoint"] = asap
    if events is not None:
        params["events_endpoint"] = events
    return card_json


async def _ingest(card_json: dict[str, Any], **trust: Any) -> Any:
    async def public_dns(hostname: str) -> list[str]:
        return ["93.184.216.34"]

    server = httpx.MockTransport(lambda request: httpx.Response(200, json=card_json))
    config = A2ATrustConfig(policy="allow_unsigned", **trust)
    ingestor = AgentCardIngestor(config, dns_resolver=public_dns, transport=server)
    return await ingestor.ingest(_CARD_URL)


_REFUSED_BY_THE_MAPPING = {
    "metadata address over http": ("http://169.254.169.254/latest/meta-data", None, "asap"),
    "file URL": ("file:///etc/passwd", None, "asap"),
    "plain http": ("http://agent.example.com/asap", None, "asap"),
    "user info": ("https://agent:secret@agent.example.com/asap", None, "asap"),
    "no host": ("https:///asap", None, "asap"),
    "events over ws": ("https://agent.example.com/asap", "ws://agent.example.com/ev", "events"),
    "events over https": (
        "https://agent.example.com/asap",
        "https://agent.example.com/ev",
        "events",
    ),
}


@pytest.mark.parametrize(
    ("asap", "events", "field"),
    list(_REFUSED_BY_THE_MAPPING.values()),
    ids=list(_REFUSED_BY_THE_MAPPING),
)
def test_the_mapping_refuses_an_endpoint_that_is_not_a_plain_https_or_wss_url(
    asap: str, events: str | None, field: str
) -> None:
    card = AgentCard.model_validate(_card_json(asap, events))

    with pytest.raises(A2AMappingError) as raised:
        agent_card_to_manifest(card)

    assert raised.value.fields == (f"endpoints.{field}",)
    assert "secret" not in str(raised.value)


_REFUSED_BY_INGEST = {
    "metadata address": ("https://169.254.169.254/latest/meta-data", None, "asap"),
    "localhost admin": ("https://localhost:8443/admin", None, "asap"),
    "loopback IPv6": ("https://[::1]/asap", None, "asap"),
    "private events": ("https://agent.example.com/asap", "wss://10.0.0.7/ev", "events"),
}


@pytest.mark.parametrize(
    ("asap", "events", "field"), list(_REFUSED_BY_INGEST.values()), ids=list(_REFUSED_BY_INGEST)
)
async def test_ingest_refuses_an_endpoint_on_a_private_or_loopback_host(
    asap: str, events: str | None, field: str
) -> None:
    with pytest.raises(A2AMappingError) as raised:
        await _ingest(_card_json(asap, events))

    assert raised.value.fields == (f"endpoints.{field}",)


async def test_the_explicit_switch_allows_a_private_endpoint() -> None:
    card_json = _card_json("https://127.0.0.1:8443/asap", "wss://127.0.0.1:8443/ev")

    result = await _ingest(card_json, allow_private_hosts=True)

    assert result.manifest.endpoints == Endpoint(
        asap="https://127.0.0.1:8443/asap", events="wss://127.0.0.1:8443/ev"
    )


async def test_a_public_https_endpoint_still_ingests() -> None:
    result = await _ingest(
        _card_json("https://agent.example.com/asap", "wss://agent.example.com/ev")
    )

    assert result.manifest.endpoints.asap == "https://agent.example.com/asap"
