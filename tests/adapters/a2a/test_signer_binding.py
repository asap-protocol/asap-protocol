"""A verified card must also be signed by a key allowed to sign for its agent.

A signature proves who signed the card, not whose card it is. With the keys of
banks A and B both trusted, a card that claims bank B, points at bank A's
server, and is signed with bank A's key must not ingest as bank B. Ingest
binds the manifest's agent URN to the scope of a signer that verified, for a
card with the ASAP extension and for a card without it (``agent_id`` from the
caller); ``verify_agent_card_for_agent`` does the same without a manifest.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    ANY_AGENT,
    A2AKeyResolver,
    A2ASignerScopeError,
    A2ATrustConfig,
    AgentCard,
    AgentCardIngestor,
    AllowedJku,
    PinnedCardKey,
    manifest_to_agent_card,
    sign_agent_card,
    verify_agent_card_for_agent,
)
from asap.models.entities import Endpoint
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_BANK_A, _BANK_B = "urn:asap:agent:bank-a", "urn:asap:agent:bank-b"
_KEY_A = ed25519.Ed25519PrivateKey.generate()
_KEY_B = ed25519.Ed25519PrivateKey.generate()
_JKU_A = "https://keys.bank-a.example/jwks.json"
_JKU_B = "https://keys.bank-b.example/jwks.json"
_CARD_URL = "https://bank-b.example/.well-known/agent-card.json"
_ON_A = Endpoint(asap="https://bank-a.example/asap")


def _jwk(key: ed25519.Ed25519PrivateKey, kid: str) -> dict[str, Any]:
    return {**OKPKey.import_key(key.public_key()).as_dict(), "kid": kid}


_PINNED_A_AND_B = A2ATrustConfig(
    pinned_keys={
        "a1": PinnedCardKey(jwk=_jwk(_KEY_A, "a1"), agents=[_BANK_A]),
        "b1": PinnedCardKey(jwk=_jwk(_KEY_B, "b1"), agents=[_BANK_B]),
    }
)


def _card(
    claims: str, key: ed25519.Ed25519PrivateKey, kid: str, *, jku: str | None = None
) -> AgentCard:
    manifest = research_manifest(id=claims, endpoints=_ON_A)
    card = manifest_to_agent_card(manifest, research_card_inputs()).card
    return sign_agent_card(card, key, kid=kid, jku=jku)


def _without_extension(card: AgentCard, key: ed25519.Ed25519PrivateKey, kid: str) -> AgentCard:
    capabilities = card.capabilities.model_copy(update={"extensions": None})
    bare = card.model_copy(update={"capabilities": capabilities, "signatures": None})
    return sign_agent_card(bare, key, kid=kid)


class _Server:
    """Serves one card at every URL and a JWKS per keys host; counts card fetches."""

    def __init__(self, card: AgentCard) -> None:
        self.card_fetches = 0
        self.transport = httpx.MockTransport(self._handle)
        self._card = card.model_dump(mode="json")

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["Host"]
        if host.startswith("keys."):
            key = _KEY_A if host == "keys.bank-a.example" else _KEY_B
            return httpx.Response(200, json={"keys": [_jwk(key, "k1")]})
        self.card_fetches += 1
        return httpx.Response(200, json=self._card)


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _ingestor(server: _Server, trust: A2ATrustConfig) -> AgentCardIngestor:
    return AgentCardIngestor(trust, dns_resolver=_public_dns, transport=server.transport)


async def test_a_card_that_claims_b_but_is_signed_by_a_is_refused_and_not_cached() -> None:
    server = _Server(_card(_BANK_B, _KEY_A, "a1"))
    ingestor = _ingestor(server, _PINNED_A_AND_B)

    for _ in range(2):
        with pytest.raises(A2ASignerScopeError) as raised:
            await ingestor.ingest(_CARD_URL)

    assert raised.value.agent_urn == _BANK_B
    assert raised.value.code == "asap:adapters/a2a/signer_scope"
    assert server.card_fetches == 2


async def test_a_card_signed_by_its_own_agent_key_ingests_with_its_signer() -> None:
    result = await _ingestor(_Server(_card(_BANK_A, _KEY_A, "a1")), _PINNED_A_AND_B).ingest(
        _CARD_URL
    )

    assert (result.verified, result.manifest.id) == (True, _BANK_A)
    assert [(s.source, s.kid, s.agents) for s in result.signers] == [("pinned", "a1", (_BANK_A,))]


async def test_two_jwks_sharing_kid_k1_bind_by_jku_and_the_error_names_it() -> None:
    trust = A2ATrustConfig(
        jku_allowlist={_JKU_A: AllowedJku(agents=[_BANK_A]), _JKU_B: AllowedJku(agents=[_BANK_B])}
    )
    forged = _Server(_card(_BANK_B, _KEY_A, "k1", jku=_JKU_A))
    genuine = _Server(_card(_BANK_B, _KEY_B, "k1", jku=_JKU_B))

    with pytest.raises(A2ASignerScopeError) as raised:
        await _ingestor(forged, trust).ingest(_CARD_URL)
    result = await _ingestor(genuine, trust).ingest(_CARD_URL)

    assert _JKU_A in str(raised.value)
    assert [(s.source, s.kid) for s in raised.value.signers] == [(_JKU_A, "k1")]
    assert [(s.source, s.kid) for s in result.signers] == [(_JKU_B, "k1")]


async def test_a_source_that_opts_in_to_any_agent_may_sign_for_any_agent() -> None:
    trust = A2ATrustConfig(
        pinned_keys={"a1": PinnedCardKey(jwk=_jwk(_KEY_A, "a1"), agents=ANY_AGENT)}
    )

    result = await _ingestor(_Server(_card(_BANK_B, _KEY_A, "a1")), trust).ingest(_CARD_URL)

    assert (result.verified, result.manifest.id) == (True, _BANK_B)


@pytest.mark.parametrize(("agent_id", "allowed"), [(_BANK_B, False), (_BANK_A, True)])
async def test_a_card_without_the_extension_binds_the_callers_agent_id(
    agent_id: str, allowed: bool
) -> None:
    card = _without_extension(_card(_BANK_A, _KEY_A, "a1"), _KEY_A, "a1")
    ingestor = _ingestor(_Server(card), _PINNED_A_AND_B)

    if not allowed:
        with pytest.raises(A2ASignerScopeError):
            await ingestor.ingest(_CARD_URL, agent_id=agent_id, endpoints=_ON_A)
        return
    result = await ingestor.ingest(_CARD_URL, agent_id=agent_id, endpoints=_ON_A)
    assert result.manifest.id == agent_id


async def test_verify_agent_card_for_agent_binds_without_a_manifest() -> None:
    resolver = A2AKeyResolver(_PINNED_A_AND_B)
    card = _card(_BANK_B, _KEY_A, "a1")

    with pytest.raises(A2ASignerScopeError):
        await verify_agent_card_for_agent(card, _BANK_B, resolver)
    result = await verify_agent_card_for_agent(card, _BANK_A, resolver)

    assert result.verified is True
