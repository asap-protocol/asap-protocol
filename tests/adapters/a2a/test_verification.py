"""Verifying a signed Agent Card under the signature policy (COMPAT-006 acceptance).

The rules are the ones in
``docs/guides/interoperability.md``, "Signed Agent Cards".
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    A2AInvalidSignatureError,
    A2ANoTrustedSignatureError,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    AgentCard,
    AgentCardSignature,
    VerifiedCardSigner,
    manifest_to_agent_card,
    sign_agent_card,
    verify_agent_card,
)
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import RecordingKeyResolver, forged_signature

_KEY = ed25519.Ed25519PrivateKey.generate()
_PUBLIC = OKPKey.import_key(_KEY.public_key())


def _signed_card() -> AgentCard:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    return sign_agent_card(card, _KEY, kid="k1")


def _add(card: AgentCard, signature: AgentCardSignature) -> AgentCard:
    return card.model_copy(update={"signatures": [*(card.signatures or []), signature]})


def _trusting_k1() -> RecordingKeyResolver:
    return RecordingKeyResolver({"k1": _PUBLIC})


async def test_a_valid_signature_passes() -> None:
    resolver = _trusting_k1()

    result = await verify_agent_card(_signed_card(), resolver)

    assert result.verified is True
    assert result.signers == (VerifiedCardSigner(source="pinned", kid="k1", agents="any_agent"),)
    assert resolver.calls == [("k1", None)]


@pytest.mark.parametrize("field", ["description", "streaming"])
async def test_a_tampered_card_fails_and_the_error_holds_no_key_material(field: str) -> None:
    card = _signed_card()
    change: dict[str, Any] = {"description": "Tampered"}
    if field == "streaming":
        change = {"capabilities": card.capabilities.model_copy(update={"streaming": False})}

    with pytest.raises(A2AInvalidSignatureError) as raised:
        await verify_agent_card(card.model_copy(update=change), _trusting_k1())

    assert (raised.value.reason, raised.value.index) == ("bad_signature", 0)
    assert raised.value.code == "asap:adapters/a2a/invalid_signature"
    assert str(_PUBLIC.as_dict()["x"]) not in f"{raised.value} {raised.value.details}"


async def test_a_card_signed_only_by_an_unknown_kid_has_no_trusted_signature() -> None:
    with pytest.raises(A2ANoTrustedSignatureError, match="'k1' is not trusted") as raised:
        await verify_agent_card(_signed_card(), RecordingKeyResolver({}))

    assert raised.value.code == "asap:adapters/a2a/no_trusted_signature"


@pytest.mark.parametrize("signatures", [None, []])
async def test_an_unsigned_card_fails_under_the_default_policy(
    signatures: list[Any] | None,
) -> None:
    card = _signed_card().model_copy(update={"signatures": signatures})

    with pytest.raises(A2AUnsignedCardError) as raised:
        await verify_agent_card(card, _trusting_k1())

    assert raised.value.code == "asap:adapters/a2a/unsigned_card"


async def test_an_unsigned_card_passes_under_allow_unsigned_marked_unverified() -> None:
    card = _signed_card().model_copy(update={"signatures": None})
    resolver = _trusting_k1()

    result = await verify_agent_card(card, resolver, policy="allow_unsigned")

    assert (result.verified, result.signers, result.card) == (False, (), card)
    assert resolver.calls == []


async def test_a_present_invalid_signature_fails_even_under_allow_unsigned() -> None:
    tampered = _signed_card().model_copy(update={"description": "Tampered"})

    with pytest.raises(A2AInvalidSignatureError):
        await verify_agent_card(tampered, _trusting_k1(), policy="allow_unsigned")


@pytest.mark.parametrize("alg", ["none", "HS256"])
async def test_none_and_hmac_fail_the_card_before_any_key_lookup(alg: str) -> None:
    card = _add(_signed_card(), forged_signature({"alg": alg, "kid": "k1"}))
    resolver = _trusting_k1()

    with pytest.raises(A2AInvalidSignatureError) as raised:
        await verify_agent_card(card, resolver)

    assert (raised.value.reason, raised.value.index) == ("forbidden_algorithm", 1)
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("alg", "allowed"),
    [("PS256", None), ("ES384", None), ("RS512", None), ("EdDSA", ("ES256", "RS256"))],
)
async def test_an_algorithm_off_the_list_is_skipped_with_no_key_lookup(
    alg: str, allowed: tuple[str, ...] | None
) -> None:
    card = _signed_card().model_copy(
        update={"signatures": [forged_signature({"alg": alg, "kid": "k1"})]}
    )
    resolver = _trusting_k1()
    options: dict[str, Any] = {"algorithms": allowed} if allowed else {}

    with pytest.raises(A2ANoTrustedSignatureError, match="is not allowed"):
        await verify_agent_card(card, resolver, **options)

    assert resolver.calls == []


async def test_a_malformed_entry_fails_the_card_before_any_key_lookup() -> None:
    resolver = _trusting_k1()
    card = _add(_signed_card(), forged_signature({"alg": "EdDSA", "kid": ""}))

    with pytest.raises(A2AInvalidSignatureError) as raised:
        await verify_agent_card(card, resolver)

    assert (raised.value.reason, raised.value.index) == ("malformed", 1)
    assert resolver.calls == []


@pytest.mark.parametrize(
    "options",
    [
        {"algorithms": ["HS256"]},
        {"algorithms": ["none"]},
        {"algorithms": ["PS256"]},
        {"algorithms": []},
        {"policy": "allow-unsigned"},
    ],
)
async def test_a_misconfigured_verifier_raises_value_error(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="algorithms|policy"):
        await verify_agent_card(_signed_card(), _trusting_k1(), **options)


_SCHEMA = "capabilities.extensions[0].params.skill_schemas.web_research.input_schema"


@pytest.mark.parametrize(
    ("change", "path"),
    [
        (
            {"properties": {"query": {"type": "string", "enum": []}}},
            f"{_SCHEMA}.properties.query.enum",
        ),
        ({"required": []}, f"{_SCHEMA}.required"),
        ({"maximum": float("inf")}, f"{_SCHEMA}.maximum"),
    ],
    ids=["empty enum", "empty required", "infinity"],
)
async def test_verify_refuses_asap_params_that_the_signature_does_not_cover(
    change: dict[str, Any], path: str
) -> None:
    """The rule once lived only in ingest, so verify then map skipped it."""
    card_json = _signed_card().model_dump(mode="json")
    card_json["capabilities"]["extensions"][0]["params"]["skill_schemas"]["web_research"][
        "input_schema"
    ].update(change)

    with pytest.raises(A2AUncoveredExtensionError) as raised:
        await verify_agent_card(AgentCard.model_validate(card_json), _trusting_k1())

    assert raised.value.paths == (path,)


async def test_uncovered_values_outside_the_asap_params_are_reported_not_refused() -> None:
    card = _signed_card()
    card_json = card.model_dump(mode="json") | {"iconUrl": ""}

    result = await verify_agent_card(AgentCard.model_validate(card_json), _trusting_k1())

    assert (result.verified, result.uncovered) == (True, ("iconUrl",))
