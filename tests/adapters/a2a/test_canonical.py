"""Canonical form of an A2A Agent Card: the bytes that a card signature covers.

The SDK side is the canonicalizer that the ``a2a-sdk`` 1.3.1 signer and verifier
both call (``docs/guides/interoperability.md``, "Signed Agent Cards").
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from a2a.utils.signing import _canonicalize_agent_card
from google.protobuf.json_format import ParseDict

from asap.adapters.a2a import AgentCard, canonicalize_agent_card
from tests.adapters.a2a.helpers import load_card_fixture


def _sdk_canonical_bytes(card_json: dict[str, Any]) -> bytes:
    return _canonicalize_agent_card(ParseDict(card_json, SdkAgentCard())).encode("utf-8")


@pytest.mark.parametrize("name", ["minimal_card.json", "full_card.json", "edge_card.json"])
def test_canonical_bytes_equal_the_sdk_canonical_bytes(name: str) -> None:
    card_json = load_card_fixture(name)

    canonical = canonicalize_agent_card(AgentCard.model_validate(card_json))

    assert canonical == _sdk_canonical_bytes(card_json)


def test_minimal_card_is_sorted_compact_and_drops_the_empty_capabilities() -> None:
    card = AgentCard.model_validate(load_card_fixture("minimal_card.json"))

    # The spec's own example keeps an empty required field; the SDKs and ASAP drop it.
    assert canonicalize_agent_card(card) == (
        b'{"defaultInputModes":["text/plain"],"defaultOutputModes":["application/json"],'
        b'"description":"Performs web research and summarization","name":"Research Agent",'
        b'"skills":[{"description":"Search the web and summarize the results",'
        b'"id":"web_research","name":"Web research","tags":["research"]}],'
        b'"supportedInterfaces":[{"protocolBinding":"JSONRPC","protocolVersion":"1.0",'
        b'"url":"https://agent.example.com/a2a"}],"version":"1.0.0"}'
    )


def test_signatures_are_left_out_of_the_canonical_form() -> None:
    card = AgentCard.model_validate(load_card_fixture("full_card.json"))
    unsigned = card.model_copy(update={"signatures": None})

    assert card.signatures
    assert canonicalize_agent_card(card) == canonicalize_agent_card(unsigned)
    assert b"signatures" not in canonicalize_agent_card(card)


def test_empty_values_go_bottom_up_and_false_values_stay() -> None:
    card = AgentCard.model_validate(load_card_fixture("edge_card.json"))

    canonical = json.loads(canonicalize_agent_card(card))
    extension = canonical["capabilities"]["extensions"][0]

    assert canonical["capabilities"]["streaming"] is False
    assert "required" not in extension
    assert "description" not in extension
    assert "tenant" not in canonical["supportedInterfaces"][0]
    assert canonical["skills"][0] == {
        "description": "Edge skill", "id": "edge", "name": "Edge", "tags": ["edge"]
    }  # fmt: skip
    assert extension["params"]["nested"] == {"keep": [1]}
    assert {"zero": 0, "no": False}.items() <= extension["params"].items()


def test_keys_sort_by_utf16_code_unit_and_numbers_take_the_ecmascript_form() -> None:
    card = AgentCard.model_validate(load_card_fixture("edge_card.json"))

    payload = canonicalize_agent_card(card)
    params = json.loads(payload)["capabilities"]["extensions"][0]["params"]

    assert list(params) == [
        "kept", "naïve", "nested", "no", "numbers", "zero", "日本語", "\U0001f600", "Ａ"
    ]  # fmt: skip
    assert b'"numbers":[1,1e+21,1e-7,0,0.1,9007199254740991]' in payload
    assert '"naïve":"café"'.encode() in payload
