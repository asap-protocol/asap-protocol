"""Work on a large card stays linear in its size.

A card may be fetched and mapped on every cache hit, so no step may do
quadratic work on card content: the skill count is capped, duplicate skill
ids are found in one pass, and a path the canonical form reports is cut, so a
chain of long keys cannot make each reported path as long as the card.
Work is counted (comparisons, characters), not timed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from asap.adapters.a2a import (
    MAX_CARD_SKILLS,
    MAX_REPORTED_PATH_LENGTH,
    A2AMappingError,
    A2ATrustConfig,
    AgentCard,
    AgentCardIngestor,
    agent_card_to_manifest,
    canonical_card_form,
    manifest_to_agent_card,
)
from asap.adapters.a2a.mapping import _require_unique_skill_ids
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest


def _card_json(skill_count: int) -> dict[str, Any]:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    card_json = card.model_dump(mode="json")
    card_json["skills"] = [
        {"id": f"skill-{n}", "name": "n", "description": "d", "tags": ["t"]}
        for n in range(skill_count)
    ]
    del card_json["capabilities"]["extensions"][0]["params"]["skill_schemas"]
    return card_json


class _CountingId(str):
    """A skill id that counts how often it is compared for equality."""

    comparisons = 0

    def __eq__(self, other: object) -> bool:
        type(self).comparisons += 1
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def test_duplicate_skill_ids_are_found_without_comparing_every_pair() -> None:
    ids = [_CountingId(f"skill-{n}") for n in range(MAX_CARD_SKILLS)]
    _CountingId.comparisons = 0

    _require_unique_skill_ids(ids, "Agent Card 'big'")

    assert _CountingId.comparisons <= MAX_CARD_SKILLS


def test_a_card_at_the_skill_cap_maps() -> None:
    manifest = agent_card_to_manifest(AgentCard.model_validate(_card_json(MAX_CARD_SKILLS)))

    assert len(manifest.manifest.capabilities.skills) == MAX_CARD_SKILLS


def test_a_card_over_the_skill_cap_does_not_parse() -> None:
    with pytest.raises(ValidationError, match="skills"):
        AgentCard.model_validate(_card_json(MAX_CARD_SKILLS + 1))


async def test_ingest_refuses_a_card_over_the_skill_cap_with_a_typed_error() -> None:
    body = _card_json(MAX_CARD_SKILLS + 1)
    server = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    async def public_dns(hostname: str) -> list[str]:
        return ["93.184.216.34"]

    ingestor = AgentCardIngestor(
        A2ATrustConfig(policy="allow_unsigned"), dns_resolver=public_dns, transport=server
    )

    with pytest.raises(A2AMappingError) as raised:
        await ingestor.ingest("https://agent.example.com/.well-known/agent-card.json")

    assert raised.value.fields == ("skills",)


def test_a_reported_path_is_cut_so_long_keys_cannot_multiply_the_work() -> None:
    leaves: dict[str, Any] = {f"empty-{n}": "" for n in range(2000)}
    leaves["kept"] = 1
    chain: dict[str, Any] = leaves
    for level in range(50):
        chain = {f"{'k' * 2000}{level}": chain}
    card_json = _card_json(1)
    card_json["capabilities"]["extensions"].append({"uri": "urn:example:x", "params": chain})

    uncovered = canonical_card_form(AgentCard.model_validate(card_json)).uncovered

    assert len(uncovered) == 2000
    assert max(len(path) for path in uncovered) <= MAX_REPORTED_PATH_LENGTH + 1
    assert all(path.startswith("capabilities.extensions[1].params.kkk") for path in uncovered)
