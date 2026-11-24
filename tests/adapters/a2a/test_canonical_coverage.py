"""What the canonical form of an Agent Card does not cover, and when it has no form.

A signer refuses a card with an uncovered value; a verifier reports the paths
(``docs/guides/interoperability.md``, "Signed Agent Cards").
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from a2a.utils._jcs import CanonicalizationError
from a2a.utils.signing import _canonicalize_agent_card
from google.protobuf.json_format import ParseDict

from asap.adapters.a2a import (
    MAX_CARD_NESTING_DEPTH,
    A2ACanonicalFormError,
    AgentCard,
    canonical_card_form,
    canonicalize_agent_card,
)
from tests.adapters.a2a.helpers import load_card_fixture

_PARAMS = "capabilities.extensions[0].params"
_PARAMS_DEPTH = 5
"""Nesting depth of ``params``: card, capabilities, extension list, extension, params."""


def _sdk_canonical_bytes(card_json: dict[str, Any]) -> bytes:
    return _canonicalize_agent_card(ParseDict(card_json, SdkAgentCard())).encode("utf-8")


def _card_with_params(params: dict[str, Any]) -> dict[str, Any]:
    card = load_card_fixture("minimal_card.json")
    card["capabilities"] = {"extensions": [{"uri": "https://example.com/ext", "params": params}]}
    return card


def _nested_params(levels: int) -> dict[str, Any]:
    params: dict[str, Any] = {"leaf": 1}
    for _ in range(levels):
        params = {"next": params}
    return params


def test_uncovered_lists_each_value_that_rule_5_removes_by_its_outermost_path() -> None:
    form = canonical_card_form(AgentCard.model_validate(load_card_fixture("edge_card.json")))

    assert form.payload == canonicalize_agent_card(
        AgentCard.model_validate(load_card_fixture("edge_card.json"))
    )
    assert form.uncovered == (
        "supportedInterfaces[0].tenant",
        "documentationUrl",
        "capabilities.extensions[0].description",
        f"{_PARAMS}.emptyString",
        f"{_PARAMS}.emptyList",
        f"{_PARAMS}.emptyObject",
        f"{_PARAMS}.nothing",
        f"{_PARAMS}.nested.inner",
        f"{_PARAMS}.nested.keep[1]",
        f"{_PARAMS}.nested.keep[2]",
        "skills[0].examples",
        "skills[0].inputModes",
    )


def test_an_empty_required_message_is_uncovered_and_a_full_card_has_nothing_uncovered() -> None:
    minimal = AgentCard.model_validate(load_card_fixture("minimal_card.json"))
    full = AgentCard.model_validate(load_card_fixture("full_card.json"))

    assert canonical_card_form(minimal).uncovered == ("capabilities",)
    assert canonical_card_form(full).uncovered == ()


@pytest.mark.parametrize("value", [2**53, -(2**53), 2**64])
def test_an_integer_with_no_exact_json_number_is_uncovered(value: int) -> None:
    card_json = _card_with_params({"n": value, "max": 2**53 - 1, "min": -(2**53 - 1)})

    form = canonical_card_form(AgentCard.model_validate(card_json))

    assert form.uncovered == (f"{_PARAMS}.n",)
    assert form.payload == _sdk_canonical_bytes(card_json)


def test_a_non_finite_number_dumps_as_null_and_is_uncovered() -> None:
    card = AgentCard.model_validate(_card_with_params({"n": float("nan"), "kept": 1}))

    assert canonical_card_form(card).uncovered == (f"{_PARAMS}.n",)


def test_nesting_at_the_sdk_limit_canonicalizes_and_one_more_level_fails_on_both_sides() -> None:
    at_limit = _card_with_params(_nested_params(MAX_CARD_NESTING_DEPTH - _PARAMS_DEPTH))
    past_limit = _card_with_params(_nested_params(MAX_CARD_NESTING_DEPTH - _PARAMS_DEPTH + 1))

    at_limit_bytes = canonicalize_agent_card(AgentCard.model_validate(at_limit))

    assert at_limit_bytes == _sdk_canonical_bytes(at_limit)
    with pytest.raises(A2ACanonicalFormError, match="deeper than 128"):
        canonicalize_agent_card(AgentCard.model_validate(past_limit))
    with pytest.raises(CanonicalizationError):
        _sdk_canonical_bytes(past_limit)


def test_nesting_past_the_model_serializer_limit_raises_the_typed_error() -> None:
    card = AgentCard.model_validate(_card_with_params(_nested_params(400)))

    with pytest.raises(A2ACanonicalFormError, match="model serializer"):
        canonical_card_form(card)


def test_text_that_is_not_valid_unicode_has_no_canonical_form() -> None:
    card = AgentCard.model_validate(_card_with_params({"lone": "\ud800"}))

    with pytest.raises(A2ACanonicalFormError, match="Unicode") as raised:
        canonicalize_agent_card(card)

    assert raised.value.code == "asap:adapters/a2a/canonical_form"
