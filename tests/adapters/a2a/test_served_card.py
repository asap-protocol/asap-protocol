"""The card for a served agent: mapping, card security, and signing in one call.

The card verifies with ASAP's own verifier and in ``a2a-sdk``. The
default is one ``EdDSA`` signature; an ``ES256`` signature is an opt-in for
verifiers that allow only ``ES256`` and ``RS256``. Skill schemas with empty
values are refused, never normalized.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import AgentCard as SdkAgentCard
from a2a.utils.signing import InvalidSignaturesError, create_signature_verifier
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from google.protobuf.json_format import ParseDict
from joserfc.jwk import ECKey, OKPKey

from asap.adapters.a2a import (
    A2ACardNotSignableError,
    A2AKeyResolver,
    A2AMappingInputError,
    A2ATrustConfig,
    AgentCard,
    CardSigner,
    SecurityScheme,
    agent_card_to_manifest,
    build_signed_agent_card,
    manifest_to_agent_card,
    verify_agent_card,
)
from asap.auth.external_tokens import ExternalTokenConfig
from asap.models.entities import Skill
from tests.adapters.a2a.helpers import (
    research_card_inputs,
    research_manifest,
    tagged_card_inputs,
)
from tests.adapters.a2a.signing_helpers import public_pem
from tests.auth.local_issuer import LocalIssuer

_ED_KEY = ed25519.Ed25519PrivateKey.generate()
_EC_KEY = ec.generate_private_key(ec.SECP256R1())
_ED_SIGNER = CardSigner(key=_ED_KEY, kid="card-ed")
_EC_SIGNER = CardSigner(key=_EC_KEY, kid="card-ec", alg="ES256")
_TRUST = A2ATrustConfig(
    pinned_keys={
        "card-ed": {**OKPKey.import_key(_ED_KEY.public_key()).as_dict(), "kid": "card-ed"},
        "card-ec": {**ECKey.import_key(_EC_KEY.public_key()).as_dict(), "kid": "card-ec"},
    }
)
_KEYS = {"card-ed": _ED_KEY, "card-ec": _EC_KEY}


def _sdk_verify(card: AgentCard, algorithms: list[str]) -> None:
    sdk_card = ParseDict(card.model_dump(mode="json"), SdkAgentCard(), ignore_unknown_fields=False)
    create_signature_verifier(lambda kid, jku: public_pem(_KEYS[str(kid)]), algorithms)(sdk_card)


def _manifest_with_schema(schema: dict[str, Any]) -> Any:
    skills = [Skill(id="web_research", description="Search", input_schema=schema)]
    manifest = research_manifest()
    capabilities = manifest.capabilities.model_copy(update={"skills": skills})
    return manifest.model_copy(update={"capabilities": capabilities})


async def test_the_default_card_has_one_eddsa_signature_that_both_verifiers_accept() -> None:
    mapping = build_signed_agent_card(
        research_manifest(), research_card_inputs(), signers=[_ED_SIGNER]
    )

    result = await verify_agent_card(mapping.card, A2AKeyResolver(_TRUST))
    assert (result.verified, result.signer_kids, result.uncovered) == (True, ("card-ed",), ())
    _sdk_verify(mapping.card, ["EdDSA"])
    with pytest.raises(InvalidSignaturesError):
        _sdk_verify(mapping.card, ["ES256", "RS256"])
    assert agent_card_to_manifest(mapping.card).manifest == research_manifest()


async def test_an_es256_signer_is_an_opt_in_for_es256_only_verifiers() -> None:
    mapping = build_signed_agent_card(
        research_manifest(), research_card_inputs(), signers=[_ED_SIGNER, _EC_SIGNER]
    )

    result = await verify_agent_card(mapping.card, A2AKeyResolver(_TRUST))
    assert result.signer_kids == ("card-ed", "card-ec")
    _sdk_verify(mapping.card, ["ES256", "RS256"])


def test_the_mapping_reports_the_same_dropped_fields_as_the_plain_mapping() -> None:
    manifest = research_manifest(ttl_seconds=60)

    mapping = build_signed_agent_card(manifest, research_card_inputs(), signers=[_ED_SIGNER])

    assert mapping.dropped == manifest_to_agent_card(manifest, research_card_inputs()).dropped


def test_card_security_comes_from_the_external_token_config() -> None:
    idp = LocalIssuer()
    rule = {"scope": "search", "capability": "web_research"}
    tokens = ExternalTokenConfig.model_validate(
        {"issuers": [idp.config_entry("https://agent.example.com/a2a", grant_rules=[rule])]}
    )

    card = build_signed_agent_card(
        research_manifest(), research_card_inputs(), signers=[_ED_SIGNER], external_tokens=tokens
    ).card

    assert list(card.security_schemes or {}) == [idp.issuer]
    assert card.skills[0].security_requirements == card.security_requirements
    _sdk_verify(card, ["EdDSA"])


@pytest.mark.parametrize(
    "caller_security",
    [
        {
            "security_schemes": {
                "bearer": SecurityScheme.model_validate(
                    {"httpAuthSecurityScheme": {"scheme": "Bearer"}}
                )
            }
        },
        {"skill_security_requirements": {"summarize": []}},
    ],
)
def test_caller_security_and_an_external_token_config_do_not_mix(
    caller_security: dict[str, Any],
) -> None:
    tokens = ExternalTokenConfig.model_validate(
        {"issuers": [LocalIssuer().config_entry("https://agent.example.com/a2a")]}
    )

    with pytest.raises(A2AMappingInputError, match="external_tokens"):
        build_signed_agent_card(
            research_manifest(),
            research_card_inputs(**caller_security),
            signers=[_ED_SIGNER],
            external_tokens=tokens,
        )


def test_a_card_without_a_signer_is_refused() -> None:
    with pytest.raises(A2ACardNotSignableError, match="at least one signer"):
        build_signed_agent_card(research_manifest(), research_card_inputs(), signers=[])


@pytest.mark.parametrize(
    ("schema", "paths"),
    [
        # Empty values whose removal would not change validation: refused too.
        (
            {"type": "object", "properties": {}, "required": []},
            ["properties", "required"],
        ),
        # Empty values whose removal changes the schema: never dropped.
        ({"type": "string", "enum": []}, ["enum"]),
        ({"type": "string", "const": ""}, ["const"]),
        ({"type": "string", "default": None}, ["default"]),
    ],
)
def test_skill_schemas_with_empty_values_are_refused_with_every_path(
    schema: dict[str, Any], paths: list[str]
) -> None:
    prefix = "capabilities.extensions[0].params.skill_schemas.web_research.input_schema."

    manifest = _manifest_with_schema(schema)

    with pytest.raises(A2ACardNotSignableError) as caught:
        build_signed_agent_card(manifest, tagged_card_inputs(manifest), signers=[_ED_SIGNER])

    assert caught.value.paths == tuple(prefix + path for path in paths)
