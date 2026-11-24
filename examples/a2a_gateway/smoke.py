"""Smoke run of the gateway in one process: no port, and no fetch to a loopback address.

Run from the repository root::

    uv run python -m examples.a2a_gateway.smoke

The access tokens come from ``LocalIssuer`` (``tests/auth/local_issuer.py``),
whose public keys sit in the token config as a static JWKS. A real gateway
lists its real issuers instead.
"""

from __future__ import annotations

import asyncio

import httpx
from a2a.client import A2ACardResolver
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc.jwk import OKPKey

from asap.adapters.a2a import A2ATrustConfig, AgentCardIngestor, CardSigner
from asap.auth.external_tokens import ExternalTokenConfig
from examples.a2a_gateway.client import call_skill, pinned_card_verifier
from examples.a2a_gateway.demo_agent import (
    A2A_PATH,
    DEMO_GRANT_RULES,
    DEMO_SKILLS,
    demo_card_inputs,
    demo_manifest,
)
from examples.a2a_gateway.gateway import create_gateway_app
from tests.auth.local_issuer import LocalIssuer

ORIGIN = "https://gateway.example"
CARD_KID = "gateway-card-1"


async def _documentation_dns(hostname: str) -> list[str]:
    # The guarded fetch checks that the host resolves to a public address. The
    # injected ASGI transport then serves the request in process.
    return ["93.184.216.34"]


async def run_smoke() -> list[str]:
    """Run the flow against an in-process gateway and return one line per step."""
    issuer = LocalIssuer("https://issuer.example")
    card_key = Ed25519PrivateKey.generate()
    audience = f"{ORIGIN}{A2A_PATH}"
    tokens = ExternalTokenConfig.model_validate(
        {"issuers": [issuer.config_entry(audience, grant_rules=DEMO_GRANT_RULES)]}
    )
    manifest = demo_manifest(ORIGIN)
    app = create_gateway_app(
        manifest,
        demo_card_inputs(ORIGIN),
        signers=[CardSigner(key=card_key, kid=CARD_KID)],
        external_tokens=tokens,
        skills=DEMO_SKILLS,
    )
    verify_card = pinned_card_verifier(CARD_KID, card_key.public_key())
    public_jwk = {**OKPKey.import_key(card_key.public_key()).as_dict(), "kid": CARD_KID}
    # The card key may sign for this agent only.
    trust = A2ATrustConfig.model_validate(
        {"pinned_keys": {CARD_KID: {"jwk": public_jwk, "agents": [manifest.id]}}}
    )
    ingestor = AgentCardIngestor(
        trust,
        dns_resolver=_documentation_dns,
        transport=httpx.ASGITransport(app=app),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        card = await A2ACardResolver(http, ORIGIN).get_agent_card(signature_verifier=verify_card)
        ingested = await ingestor.ingest(f"{ORIGIN}/.well-known/agent-card.json")
        token = issuer.mint(issuer.claims(audience, scope="demo:greet"))
        reply = await call_skill(
            http,
            ORIGIN,
            verify_card=verify_card,
            token=token,
            skill_id="greet",
            arguments={"name": "Ada", "language": "en"},
        )
    verified = "card verified" if ingested.verified else "card NOT verified"
    return [
        f"a2a-sdk client: card {card.name!r} verified (EdDSA, kid {CARD_KID})",
        f"ASAP ingest: {verified}, manifest {ingested.manifest.id}",
        f"greet with scope demo:greet: {reply}",
    ]


def main() -> None:
    """Print each step of the smoke run."""
    for line in asyncio.run(run_smoke()):
        print(line)


if __name__ == "__main__":
    main()
