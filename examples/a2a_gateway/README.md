# A2A gateway (reference example)

This example puts an A2A v1.0 interface in front of an ASAP agent. It is the v2.6.0 A2A flow: an A2A client reads the agent's signed Agent Card, verifies it, and calls one skill with an external OAuth access token. The call runs only under an ASAP capability grant.

This is a reference example. It has no support tier. It uses the A2A Python SDK (`a2a-sdk`) and only public functions of `asap-protocol`. The `asap.adapters.a2a` package itself does not import `a2a-sdk`.

## Prerequisites

- Python 3.13 and [uv](https://github.com/astral-sh/uv).
- From the repository root, run `uv sync`. The dev dependency group holds `a2a-sdk[signing]`. Outside this repository, install `asap-protocol` and `a2a-sdk[signing]>=1.3,<2`.

## Smoke run

Run this command from the repository root:

```bash
ASAP_LOG_LEVEL=ERROR uv run python -m examples.a2a_gateway.smoke
```

Expected output:

```text
a2a-sdk client: card 'A2A Gateway Demo' verified (EdDSA, kid gateway-card-1)
ASAP ingest: card verified, manifest urn:asap:agent:a2a-gateway-demo
greet with scope demo:greet: {'greeting': 'Hello, Ada!'}
```

Remove `ASAP_LOG_LEVEL=ERROR` to also see the log events, for example `a2a_gateway.skill_call` with the issuer, the subject, the skill, and the outcome.

The run starts the gateway in the same process and talks to it over `httpx.ASGITransport`. It opens no port and fetches nothing from a loopback address. The access tokens come from the test issuer `LocalIssuer` (`tests/auth/local_issuer.py`). The token config holds its public keys as a static JWKS, so the gateway fetches no keys either. A real gateway lists its real issuers instead.

## What the smoke run does

1. It builds the gateway with `create_gateway_app` (`gateway.py`). The gateway makes its Agent Card from the ASAP manifest with `build_signed_agent_card`, signs it with an Ed25519 key, and serves it at `/.well-known/agent-card.json`, beside the manifest at `/.well-known/asap/manifest.json`.
2. An `a2a-sdk` client fetches the card and verifies its signature with a verifier that trusts one pinned key id (`client.py`). The verifier never follows a `jku` URL from the card.
3. ASAP's own ingest (`AgentCardIngestor`) fetches the same card, verifies it, and maps it back to the manifest.
4. The client calls the skill `greet` through `POST /a2a` with a token that has the scope `demo:greet`. The gateway verifies the token, selects the skill, checks the grant, and runs the skill.

## Card signatures

The card has one `EdDSA` signature by default. The `a2a-sdk` verifier accepts it when its algorithm list holds `EdDSA`. A verifier that allows only `ES256` and `RS256` (the list in the SDK docstring) rejects a card with only an `EdDSA` signature. To serve such verifiers, add a second signer with a P-256 key:

```python
signers = [
    CardSigner(key=ed25519_key, kid="gateway-card-1"),
    CardSigner(key=p256_key, kid="gateway-card-ec-1", alg="ES256"),
]
```

## Files

| File | What it holds |
|------|---------------|
| `demo_agent.py` | The demo ASAP agent: manifest, card inputs, grant rules, and the skills `echo` and `greet` |
| `gateway.py` | `create_gateway_app`: the served card, the token check, and the `a2a-sdk` JSON-RPC server |
| `skill_call.py` | Skill selection from the message metadata, and the grant check |
| `client.py` | The `a2a-sdk` client side: the pinned card verifier and `call_skill` |
| `smoke.py` | The smoke run above |

The tests are in `tests/examples/test_a2a_gateway*.py`.
