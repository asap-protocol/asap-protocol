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
3. ASAP's own ingest (`AgentCardIngestor`) fetches the same card, verifies it, and maps it back to the manifest. Its trust config pins the card key for the demo agent's URN only.
4. The client calls the skill `greet` through `POST /a2a` with a token that has the scope `demo:greet`. The gateway verifies the token, selects the skill, checks the grant, and runs the skill.

## Serve over HTTP

Run this command from the repository root:

```bash
uv run python -m examples.a2a_gateway.serve --port 8788
```

The gateway then serves its card, its manifest, and `POST /a2a` at `http://127.0.0.1:8788`. That origin is in the card, in the token audience (`http://127.0.0.1:8788/a2a`), and in the interface URL.

- **Loopback only.** `--host` takes a loopback IP address: `127.0.0.1` (the default), another `127.x.x.x` address, or `::1`. Any other value stops the command before it opens a port.
- **Local requests only.** The gateway answers HTTP 403 when the `Host` header does not name a loopback address or `localhost`, or when an `Origin` header names another host. So a web page cannot reach the gateway through DNS rebinding. This is the stance of `examples/mcp_auth_http`.
- **No tokens.** The command creates a new `LocalIssuer` and a new card key at each start. Nobody outside the process can get a token from that issuer, so the gateway refuses every skill call in this mode.

The demo agent's URN is fixed: `urn:asap:agent:a2a-gateway-demo` (`DEMO_AGENT_URN` in `demo_agent.py`).

## Compliance mode (test only)

> **Warning:** compliance mode hands out valid tokens to anyone who can reach the gateway. Use it only for a compliance run on a local machine or in CI. It is off by default, and only the command-line flag turns it on; no environment variable does.

```bash
uv run python -m examples.a2a_gateway.serve --port 8788 --compliance
```

In this mode, each `POST /compliance/probe-tokens` returns a fresh set as JSON:

- `a2a_url`, `skill_id` (`greet`), `allowed_arguments` (`{"name": "Ada", "language": "en"}`), and `constraint_violation_arguments` (`"language": "fr"`, which the grant does not allow).
- `external.valid`: a token with the scope `demo:greet`. `external.out_of_grant`: a token with the scope `demo:echo`, a grant for another skill. `external.wrong_audience`: a token with the scope `demo:greet` for the audience `<origin>/not-this-gateway`.
- `card_key`: the `kid` and the public JWK of the card key made at this start.

The shape is the one of `examples/mcp_auth_http`, so the `ComplianceEndpointTokenProvider` of `asap-compliance` reads it unchanged. Without the flag, the path does not exist (HTTP 404).

The `a2a-bridge` compliance profile takes the card key and the agent URN from its own config, never from the gateway. In a demo run you copy `card_key` into that config. That step exists only because the demo makes a new key at each start. For a real gateway, pin the key that the agent's owner publishes.

## Walk-through

The steps below use the demo agent of `demo_agent.py`. Each request and answer is the one the tests send and check.

### 1. Build the gateway

The gateway needs four inputs: the ASAP manifest, the card data a manifest does not hold, the external-token config, and a card signing key.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.adapters.a2a import CardSigner
from asap.auth.external_tokens import ExternalTokenConfig
from examples.a2a_gateway.demo_agent import DEMO_GRANT_RULES, DEMO_SKILLS, demo_card_inputs, demo_manifest
from examples.a2a_gateway.gateway import create_gateway_app

origin = "https://gateway.example"
tokens = ExternalTokenConfig.model_validate({"issuers": [{
    "issuer": "https://issuer.example",
    "audience": f"{origin}/a2a",
    "key_source": {"kind": "oidc_discovery"},
    "grant_rules": list(DEMO_GRANT_RULES),
}]})
app = create_gateway_app(
    demo_manifest(origin),
    demo_card_inputs(origin),
    signers=[CardSigner(key=Ed25519PrivateKey.generate(), kid="gateway-card-1")],
    external_tokens=tokens,
    skills=DEMO_SKILLS,
)
```

Each grant rule maps one scope to one skill. The skill `id` is the capability name. `greet` also has a constraint: its `language` argument must be `en` or `pt`. A scope with no rule grants nothing.

### 2. Discover the agent

The gateway serves two documents from one origin:

- `GET /.well-known/asap/manifest.json`: the ASAP manifest.
- `GET /.well-known/agent-card.json`: the A2A v1.0 Agent Card, signed. Its ASAP extension (`https://asap-protocol.com/a2a/extensions/manifest/v1`) holds the manifest `id` as `agent_urn`.

Both answers have `Cache-Control: public, max-age=300` and an `ETag`. The card states what the gateway enforces. Each token issuer is one OpenID Connect scheme, and each scope that grants a skill is one requirement on that skill:

```json
"securitySchemes": {
  "https://issuer.example": {
    "openIdConnectSecurityScheme": {
      "description": "Bearer access token from https://issuer.example with audience https://gateway.example/a2a",
      "openIdConnectUrl": "https://issuer.example/.well-known/openid-configuration"
    }
  }
}
```

The skill `greet` lists `{"schemes": {"https://issuer.example": {"list": ["demo:greet"]}}}`. The card-level list holds every such requirement.

### 3. Verify the card

Verify the card before you use it. `client.py` shows the `a2a-sdk` side: `pinned_card_verifier` trusts one key id and never follows a `jku` URL from the card. ASAP's `AgentCardIngestor` verifies the same card under an `A2ATrustConfig` and maps it back to the manifest.

A signature proves who signed the card. It does not prove whose card it is. So each trusted key in an `A2ATrustConfig` names the agents it may sign for, and ingest accepts the card only when a key that verified may sign for the card's agent URN:

```python
from asap.adapters.a2a import A2ATrustConfig, AgentCardIngestor, PinnedCardKey

trust = A2ATrustConfig(
    pinned_keys={
        "gateway-card-1": PinnedCardKey(
            jwk=gateway_card_public_jwk,  # the public JWK of the card key, with "kid": "gateway-card-1"
            agents=("urn:asap:agent:a2a-gateway-demo",),
        )
    }
)
result = await AgentCardIngestor(trust).ingest("https://gateway.example/.well-known/agent-card.json")
```

`agents` is a tuple of agent URNs in code (a list in a JSON or YAML config read with `A2ATrustConfig.model_validate`). The same card, checked with a key pinned for another agent, fails with `A2ASignerScopeError`. A key that may sign for every agent needs the explicit value `agents=ANY_AGENT`. A `verified` card is still not bound to the URL it came from, and it has no expiry: it can be served again from any URL while its key is trusted.

### 4. Call a skill

The interface is A2A JSON-RPC at `POST /a2a`. Send the headers `A2A-Version: 1.0` and `Authorization: Bearer <access token>`. The token must come from a configured issuer and name the gateway's audience. Select the skill with the message metadata key `https://asap-protocol.com/a2a/extensions/manifest/v1`, and put the arguments in one `data` part:

```json
{"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": {
  "messageId": "8e954b92-2f1e-4da8-be79-83dd0328cd8f",
  "role": "ROLE_USER",
  "parts": [{"data": {"name": "Ada", "language": "en"}}],
  "metadata": {"https://asap-protocol.com/a2a/extensions/manifest/v1": {"skill_id": "greet"}}
}}}
```

With a token that has the scope `demo:greet`, the answer is HTTP 200 with one message:

```json
{"jsonrpc": "2.0", "id": "1", "result": {"message": {"role": "ROLE_AGENT", "parts": [{"data": {"greeting": "Hello, Ada!"}}], "messageId": "…", "contextId": "…", "taskId": "…"}}}
```

When the agent has only one skill, a message without the metadata key runs that skill. The gateway serves `SendMessage` only. It keeps no tasks.

### 5. Refusals

The gateway checks the token first, then the skill selection, then the grant. A refused call never reaches the skill.

| Case | HTTP | `WWW-Authenticate` | Body |
|------|------|--------------------|------|
| No token | 401 | `Bearer` | `{"error":"invalid_token","error_description":"asap:auth_required"}` |
| Token refused (wrong audience, expired, unknown issuer, an ASAP Agent JWT, and so on) | 401 | `Bearer error="invalid_token", error_description="asap:invalid_token"` | `{"error":"invalid_token","error_description":"asap:invalid_token"}` |
| Issuer keys cannot be fetched | 503 | none | `{"error":"temporarily_unavailable","error_description":"Authentication service unavailable"}` |
| No grant for the selected skill | 403 | `Bearer error="insufficient_scope", error_description="asap:capability_denied"` | `{"error":"insufficient_scope","error_description":"asap:capability_denied"}` |
| Arguments outside the grant's constraints | 403 | none | `{"error":"access_denied","error_description":"asap:constraint_violation","violations":[{"field":"language","operator":"in"}]}` |
| No skill selected, an unknown skill, or arguments that are not one JSON object | 200 | none | JSON-RPC error `-32602` |
| A method other than `SendMessage` | 200 | none | JSON-RPC error `-32004` |

The four `asap:*` codes are the same strings as in the MCP Auth Bridge. The `a2a-sdk` client raises `A2AClientError` for a 401, 403, or 503 answer; its `__cause__` is the `httpx.HTTPStatusError` with the status, the headers, and the body. The answers never hold the token. The gateway logs the reason of a refused token, never the token itself.

Run the allowed path and every refusal:

```bash
uv run pytest tests/examples/test_a2a_gateway.py tests/examples/test_a2a_gateway_skill_call.py tests/examples/test_a2a_gateway_denied.py tests/examples/test_a2a_gateway_smoke.py -q
```

## What the gateway does not do

- It does not accept an ASAP Agent JWT or Host JWT as an A2A credential.
- It does not send the caller's token anywhere. The `a2a-sdk` layer and the skills do not see the `Authorization` header.
- It does not check the arguments against the skill's JSON Schema. Only the grant constraints are checked.
- It keeps no task, and it has no streaming or push notifications.
- It registers no ASAP handler: `POST /asap` (the endpoint that the card's ASAP extension names) answers every task with an error, so a skill runs only through `POST /a2a` and its checks.

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
| `serve.py` | The HTTP server on a loopback address, with the `Host` and `Origin` checks |
| `compliance.py` | The test-only probe path of compliance mode |

The tests are in `tests/examples/test_a2a_gateway*.py`.
