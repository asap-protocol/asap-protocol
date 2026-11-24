"""The A2A flow through ``examples/a2a_gateway/``.

The gateway serves a signed A2A v1.0 Agent Card beside the ASAP manifest.
An ``a2a-sdk`` client resolves the card with a signature verifier, and ASAP's
own ingest pipeline verifies the same served card and maps it back to
the gateway's manifest. The client then calls one skill with an access token
from the local issuer, under the grant its scope maps to. Everything runs
in process over ``httpx.ASGITransport``.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from a2a.client import A2ACardResolver
from a2a.utils.signing import InvalidSignaturesError
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI
from joserfc.jwk import OKPKey

from asap.adapters.a2a import A2ATrustConfig, AgentCardIngestor, CardSigner
from asap.auth.external_tokens import ExternalTokenConfig
from examples.a2a_gateway.client import call_skill, pinned_card_verifier
from examples.a2a_gateway.demo_agent import (
    A2A_PATH,
    DEMO_GRANT_RULES,
    DEMO_SKILLS,
    SkillHandler,
    demo_card_inputs,
    demo_manifest,
)
from examples.a2a_gateway.gateway import create_gateway_app
from tests.auth.local_issuer import LocalIssuer

ORIGIN = "https://gateway.example"
AUDIENCE = f"{ORIGIN}{A2A_PATH}"
CARD_KID = "gateway-card-1"
CARD_KEY = ed25519.Ed25519PrivateKey.generate()
ISSUER = LocalIssuer("https://issuer.example")


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def build_gateway(skills: Mapping[str, SkillHandler] = DEMO_SKILLS) -> FastAPI:
    tokens = ExternalTokenConfig.model_validate(
        {"issuers": [ISSUER.config_entry(AUDIENCE, grant_rules=DEMO_GRANT_RULES)]}
    )
    return create_gateway_app(
        demo_manifest(ORIGIN),
        demo_card_inputs(ORIGIN),
        signers=[CardSigner(key=CARD_KEY, kid=CARD_KID)],
        external_tokens=tokens,
        skills=skills,
    )


@pytest.fixture
def gateway() -> FastAPI:
    return build_gateway()


@pytest.fixture
async def http(gateway: FastAPI) -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN
    ) as client:
        yield client


async def test_the_sdk_client_resolves_and_verifies_the_served_card(
    http: httpx.AsyncClient,
) -> None:
    resolver = A2ACardResolver(http, ORIGIN)

    card = await resolver.get_agent_card(
        signature_verifier=pinned_card_verifier(CARD_KID, CARD_KEY.public_key())
    )

    assert [skill.id for skill in card.skills] == ["echo", "greet"]
    assert card.supported_interfaces[0].url == AUDIENCE
    assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert list(card.security_schemes) == [ISSUER.issuer]


async def test_a_card_from_an_unpinned_key_fails_in_the_sdk_verifier(
    http: httpx.AsyncClient,
) -> None:
    other_key = ed25519.Ed25519PrivateKey.generate().public_key()
    resolver = A2ACardResolver(http, ORIGIN)

    with pytest.raises(InvalidSignaturesError):
        await resolver.get_agent_card(signature_verifier=pinned_card_verifier(CARD_KID, other_key))


async def test_asap_ingest_verifies_the_served_card_and_maps_it_to_the_manifest(
    gateway: FastAPI,
) -> None:
    public_jwk = {**OKPKey.import_key(CARD_KEY.public_key()).as_dict(), "kid": CARD_KID}
    ingestor = AgentCardIngestor(
        A2ATrustConfig(pinned_keys={CARD_KID: public_jwk}),
        dns_resolver=_public_dns,
        transport=httpx.ASGITransport(app=gateway),
    )

    result = await ingestor.ingest(f"{ORIGIN}/.well-known/agent-card.json")

    assert (result.verified, result.signer_kids, result.uncovered) == (True, (CARD_KID,), ())
    assert result.manifest == demo_manifest(ORIGIN)


async def test_the_manifest_and_the_card_describe_the_same_agent(http: httpx.AsyncClient) -> None:
    manifest = (await http.get("/.well-known/asap/manifest.json")).json()
    card = (await http.get("/.well-known/agent-card.json")).json()

    assert (card["name"], card["version"]) == (manifest["name"], manifest["version"])
    extension = card["capabilities"]["extensions"][0]
    assert extension["params"]["agent_urn"] == manifest["id"]


def test_the_skills_must_match_the_manifest() -> None:
    with pytest.raises(ValueError, match="must match the manifest skills"):
        build_gateway(skills={"echo": DEMO_SKILLS["echo"]})


async def test_an_a2a_client_calls_a_skill_with_an_external_token_under_an_asap_grant(
    http: httpx.AsyncClient,
) -> None:
    token = ISSUER.mint(ISSUER.claims(AUDIENCE, scope="demo:greet"))

    reply = await call_skill(
        http,
        ORIGIN,
        verify_card=pinned_card_verifier(CARD_KID, CARD_KEY.public_key()),
        token=token,
        skill_id="greet",
        arguments={"name": "Ada", "language": "pt"},
    )

    assert reply == {"greeting": "Olá, Ada!"}


def _rpc_headers(scope: str) -> dict[str, str]:
    token = ISSUER.mint(ISSUER.claims(AUDIENCE, scope=scope))
    return {"Authorization": f"Bearer {token}", "A2A-Version": "1.0"}


@pytest.mark.parametrize(
    ("method", "params", "code", "text"),
    [
        ("ListTasks", {}, -32004, "SendMessage only"),
        ("SendMessage", {"message": {"parts": [{"data": {}}]}}, -32602, "no skill selected"),
    ],
)
async def test_after_authentication_only_a_selected_skill_call_reaches_the_sdk(
    http: httpx.AsyncClient, method: str, params: dict[str, object], code: int, text: str
) -> None:
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}

    response = await http.post(A2A_PATH, json=body, headers=_rpc_headers("demo:echo"))

    assert (response.status_code, response.json()["id"]) == (200, 7)
    assert response.json()["error"]["code"] == code
    assert text in response.json()["error"]["message"]
