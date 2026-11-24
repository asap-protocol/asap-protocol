# Interoperability: A2A, MCP, and ASAP

ASAP layers on A2A v1.0 and MCP authorization (OAuth 2.1). It does not replace them. An A2A client keeps sending A2A messages, and an MCP client keeps sending MCP requests. ASAP adds a check on the server side: before a skill or a tool runs, the caller must hold an ASAP *grant* for it.

This guide shows two flows that run end to end in this repository:

- **Flow A (A2A):** an A2A client reads the signed Agent Card of an ASAP agent, verifies it, and calls one skill with an OAuth access token. An ASAP grant decides whether the skill runs.
- **Flow B (MCP):** an MCP client calls a tool on an HTTP MCP server. `@asap-protocol/mcp-auth` checks the token and the grant before the tool runs.

The guide describes `asap-protocol` 2.6.0 and `@asap-protocol/mcp-auth` 0.1.0.

## Start here

| You have | You want | Read |
|----------|----------|------|
| An A2A v1.0 client | To call a skill of an ASAP agent | [Flow A](#flow-a-an-a2a-client-calls-an-asap-agent) |
| An ASAP agent | To be found and called by A2A clients | [Flow A](#flow-a-an-a2a-client-calls-an-asap-agent), then [Signed Agent Cards](#signed-agent-cards) |
| An Agent Card from another party | To verify it and read it as an ASAP manifest | [Verify a card that you fetch](#step-3-verify-a-card-that-you-fetch) |
| A Python MCP server on stdio | Agent JWTs checked against grants | [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) |

## Terms

| Term | Meaning |
|------|---------|
| **Agent** | A program that acts for a person or a service. In ASAP, each agent has a URN, such as `urn:asap:agent:research`. |
| **Host** | The app or service that runs an agent and holds its keys. |
| **Manifest** | ASAP's description of an agent: its URN, name, version, skills, and the URL of its ASAP endpoint. It is served at `/.well-known/asap/manifest.json`. |
| **Agent Card** | A2A's description of an agent. It is served at `/.well-known/agent-card.json`. A card can carry signatures. |
| **Skill** | One task that an agent can do, such as `web_research`. A2A and ASAP both use this word. |
| **Capability** | A named permission, such as `file:read`. In the flows of this guide, each skill or tool needs one capability. |
| **Grant** | Gives one caller one capability, with optional constraints. With no grant, the call is refused. |
| **Constraint** | A rule on the arguments of a call, inside a grant: an exact value, or the operators `max`, `min`, `in`, and `not_in`. |
| **Agent JWT** | ASAP's own token. An ASAP agent signs a new one for each request with its Ed25519 key. It lives 60 seconds and works once. |
| **Access token** | An OAuth 2.1 access token from an authorization server (the *issuer*) that you list. This guide also calls it an *external token*. |
| **Principal** | The verified caller of one request. For an access token, ASAP builds the principal from the token and keeps it for that request only. |

## Where ASAP sits

ASAP reuses the base standard at each layer and adds identity, grants, and conformance checks on top.

| Layer | Base standard | What ASAP adds |
|-------|---------------|----------------|
| Interaction | A2A v1.0 tasks and messages | The ASAP envelope and task state machine, ASAP's own wire format (the flows in this guide do not use it). For A2A callers: a reference gateway that checks an ASAP grant before a skill runs (`examples/a2a_gateway/`) |
| Tool execution | MCP tools | The MCP Auth Bridge: an Agent JWT or an access token, and a grant, on each `tools/call`. Python covers stdio servers; `@asap-protocol/mcp-auth` covers Streamable HTTP servers in TypeScript |
| Discovery | A2A Agent Card | The manifest and its well-known URI; a signed Agent Card built from the manifest and served beside it; verification of the cards that you fetch |
| Identity and access | OAuth 2.1 and OpenID Connect; MCP authorization (OAuth 2.1) | Host and Agent JWTs, capability grants, approval flows; grants for access tokens from issuers that you list |
| Conformance | None | `asap-compliance` checks |

## Pinned versions

This release is built and tested against these versions. The A2A and MCP rules that this guide describes come from them.

| What | Version |
|------|---------|
| A2A specification | 1.0, from `specification/a2a.proto` at tag `v1.0.1` |
| A2A Python SDK (`a2a-sdk`) | 1.3.1. Only the tests and the reference gateway use it; `asap-protocol` does not depend on it |
| MCP specification | Revision `2026-07-28` |
| MCP TypeScript SDK | `@modelcontextprotocol/server` 2.4.0. `@asap-protocol/mcp-auth` accepts 2.4.0 and later 2.x releases |

## Flow A: an A2A client calls an ASAP agent

### Who trusts whom

```text
A2A client                                    Gateway in front of an ASAP agent
    │                                                    │
    │ 1. GET /.well-known/agent-card.json                │
    │ ─────────────────────────────────────────────────► │
    │ ◄───────────────────────────── signed Agent Card   │
    │ 2. verify the card with a key that you trust       │
    │                                                    │
    │ 3. SendMessage, Authorization: Bearer <token>      │
    │ ─────────────────────────────────────────────────► │ 4. verify the token: issuer, audience, signature, expiry
    │                                                    │ 5. turn the token into grants, with your rules
    │                                                    │ 6. run the skill only if a grant allows the call
    │ ◄──────────────────────────────── the skill result │
```

- The client trusts a card only when a key that it trusts signed the card for that agent. It never takes a key from a URL that the card names, unless you allow that URL.
- The gateway trusts only the issuers that you list, and only tokens whose audience is the gateway.
- The token's scopes grant nothing by themselves. Your grant rules decide which capability each scope gives.

What ships, and what is an example:

- **`asap.adapters.a2a`** (in `asap-protocol`, Tier 1): models of the A2A v1.0 Agent Card, the mapping between a card and a manifest, card signing, and card verification. It does not import `a2a-sdk`, and it holds no A2A server or client.
- **`create_app(agent_card=...)`** serves the signed card beside the manifest.
- **`examples/a2a_gateway/`** is a reference gateway built with the A2A Python SDK. It has no support tier: copy it and adapt it.

### Step 1: run the reference gateway

From the repository root, after `uv sync`:

```bash
ASAP_LOG_LEVEL=ERROR uv run python -m examples.a2a_gateway.smoke
```

Expected output:

```text
a2a-sdk client: card 'A2A Gateway Demo' verified (EdDSA, kid gateway-card-1)
ASAP ingest: card verified, manifest urn:asap:agent:a2a-gateway-demo
greet with scope demo:greet: {'greeting': 'Hello, Ada!'}
```

The run starts the gateway in the same process. It opens no port and fetches nothing from the network. The access tokens come from a local test issuer, whose public keys sit in the token config. The [gateway README](https://github.com/asap-protocol/asap-protocol/blob/main/examples/a2a_gateway/README.md) explains each line.

To run the allowed call and every refusal:

```bash
uv run pytest tests/examples/test_a2a_gateway.py tests/examples/test_a2a_gateway_skill_call.py tests/examples/test_a2a_gateway_denied.py tests/examples/test_a2a_gateway_smoke.py -q
```

### Step 2: build and serve a signed Agent Card

`build_signed_agent_card` makes the card from the manifest and signs it. `create_app` then serves it at `/.well-known/agent-card.json`, beside the manifest.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.adapters.a2a import AgentCardInputs, AgentInterface, CardSigner, build_signed_agent_card
from asap.auth.external_tokens import ExternalTokenConfig
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.transport.handlers import HandlerRegistry
from asap.transport.server import create_app

ORIGIN = "https://agent.example.com"

manifest = Manifest(
    id="urn:asap:agent:research",
    name="Research Agent",
    version="1.0.0",
    description="Searches the web and sums up what it finds",
    capabilities=Capability(skills=[Skill(id="web_research", description="Search the web")]),
    endpoints=Endpoint(asap=f"{ORIGIN}/asap"),
)

# The tokens that the A2A interface accepts. The card describes them.
tokens = ExternalTokenConfig.model_validate({"issuers": [{
    "issuer": "https://auth.example.com",
    "audience": f"{ORIGIN}/a2a",
    "key_source": {"kind": "oidc_discovery"},
    "grant_rules": [{"scope": "research:run", "capability": "web_research"}],
}]})

# Card data that a manifest does not hold.
inputs = AgentCardInputs(
    interfaces=[AgentInterface(url=f"{ORIGIN}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")],
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skill_tags={"web_research": ["research"]},
)

card_key = Ed25519PrivateKey.generate()  # in production, load the agent's own Ed25519 key
card = build_signed_agent_card(
    manifest, inputs, signers=[CardSigner(key=card_key, kid="research-card-1")], external_tokens=tokens
).card

app = create_app(manifest, registry=HandlerRegistry(), agent_card=card)
```

- **`inputs`** holds what a card needs and a manifest lacks: the A2A interfaces, the input and output media types, and at least one tag per skill. A missing input raises `A2AMappingInputError`.
- **`external_tokens`** writes the card's security. Each issuer becomes one OpenID Connect security scheme, and each scope rule becomes a requirement on its skill. The card can say less than the server accepts (a claim rule is not shown), never more.
- **`CardSigner`** signs with `EdDSA` by default. See [Signed Agent Cards](#signed-agent-cards).
- **`create_app`** refuses a card with no signature, a card without the ASAP extension, and a card for another agent (another URN, name, or version). It serves the card with `Content-Type: application/json`, `Cache-Control: public, max-age=300`, and an `ETag`.
- **`registry`**: put your ASAP task handlers here. Without a registry, `create_app` installs a demo echo handler on `POST /asap`.

`asap-protocol` serves the card, not the A2A interface. The A2A interface at `/a2a` comes from the A2A SDK. The reference gateway (`examples/a2a_gateway/gateway.py`) shows how to put the token check, the skill selection, and the grant check in front of the SDK's request handler.

### Step 3: verify a card that you fetch

`AgentCardIngestor` fetches a card, verifies its signatures, and maps it to an ASAP manifest. A card passes only when a key that you trust for that agent signed it.

```python
from asap.adapters.a2a import A2ATrustConfig, AgentCardIngestor, PinnedCardKey

trust = A2ATrustConfig(
    pinned_keys={
        "research-card-1": PinnedCardKey(
            jwk=research_card_public_jwk,  # the public JWK of the card key, from the agent's operator
            agents=("urn:asap:agent:research",),  # the agents that this key may sign for
        )
    }
)
ingestor = AgentCardIngestor(trust)
result = await ingestor.ingest("https://agent.example.com/.well-known/agent-card.json")
print(result.manifest.id, result.verified, [signer.kid for signer in result.signers])
```

The last line prints `urn:asap:agent:research True ['research-card-1']`.

- The fetch takes HTTPS URLs only, refuses private and loopback addresses, follows no redirect, and stops after 10 seconds or 1 MiB.
- A card that fails a check raises a typed error, a subclass of `A2AAdapterError`. It is never mapped and never cached.
- A verified card is cached by its URL for 300 seconds.
- `result.dropped` lists the card fields that the manifest does not carry (see [Agent Card and manifest fields](#agent-card-and-manifest-fields)).

A card with the ASAP extension holds the agent URN and the ASAP endpoint. For a card without it, pass them yourself: `ingest(url, agent_id="urn:asap:agent:...", endpoints=Endpoint(asap="https://..."))`.

### Step 4: call a skill

The gateway's A2A interface is JSON-RPC at `POST /a2a`. Send the headers `A2A-Version: 1.0` and `Authorization: Bearer <access token>`. An A2A message does not name a skill, so the gateway reads the skill from the message metadata key `https://asap-protocol.com/a2a/extensions/manifest/v1`. The arguments go in one `data` part:

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

- When the agent has one skill only, a message without the metadata key runs that skill.
- The skill `id` is the capability name. The grant rule `{"scope": "demo:greet", "capability": "greet", "constraints": {"language": {"in": ["en", "pt"]}}}` lets a token with the scope `demo:greet` call `greet` in English or Portuguese.
- The gateway serves `SendMessage` only, and it keeps no tasks.

### Step 5: refusals

The gateway checks the token first, then the skill selection, then the grant. A refused call never reaches the skill.

| Case | HTTP | `WWW-Authenticate` | Body |
|------|------|--------------------|------|
| No token | 401 | `Bearer` | `{"error":"invalid_token","error_description":"asap:auth_required"}` |
| Token refused: wrong audience, expired, unknown issuer, an ASAP Agent JWT, and so on | 401 | `Bearer error="invalid_token", error_description="asap:invalid_token"` | `{"error":"invalid_token","error_description":"asap:invalid_token"}` |
| The issuer's keys cannot be fetched | 503 | None | `{"error":"temporarily_unavailable","error_description":"Authentication service unavailable"}` |
| No grant for the selected skill | 403 | `Bearer error="insufficient_scope", error_description="asap:capability_denied"` | `{"error":"insufficient_scope","error_description":"asap:capability_denied"}` |
| Arguments that break a constraint of the grant | 403 | None | `{"error":"access_denied","error_description":"asap:constraint_violation","violations":[{"field":"language","operator":"in"}]}` |
| No skill selected on a card with several skills, an unknown skill, or arguments that are not one JSON object | 200 | None | JSON-RPC error `-32602` |
| A method other than `SendMessage` | 200 | None | JSON-RPC error `-32004` |

- A2A v1.0 has no error type for authentication and authorization failures, so the gateway answers them at the HTTP layer, before the A2A SDK runs. An OAuth client can read these answers without an A2A parser.
- The body and the challenge never hold the token. The reason that a token was refused goes to the log only, so the answer does not tell which check failed.
- The `a2a-sdk` client raises `A2AClientError` for a 401, 403, or 503 answer. Its `__cause__` is the `httpx.HTTPStatusError` with the status, the headers, and the body.

## Agent Card and manifest fields

`manifest_to_agent_card` maps a manifest to a card, and `agent_card_to_manifest` maps a card to a manifest. Both are pure functions: no network and no clock. Each returns the result and a `dropped` list: the source fields that the other format cannot carry, each with its reason. The tables below are also exported as data, `UNMAPPED_MANIFEST_FIELDS` and `UNMAPPED_CARD_FIELDS`.

### Mapped fields

"Extension" means the params of the ASAP extension (see [The ASAP extension](#the-asap-extension)).

| ASAP manifest | A2A Agent Card | Notes |
|---------------|----------------|-------|
| `id` | Extension `agent_urn` | A card without the extension: you pass `agent_id` |
| `name` | `name` | |
| `version` | `version` | Card to manifest: a version that the manifest does not accept raises `A2AMappingError`. The mapping does not guess |
| `description` | `description` | |
| `endpoints.asap` | Extension `asap_endpoint` | Card to manifest: must be an `https` URL. A card without the extension: you pass `endpoints` |
| `endpoints.events` | Extension `events_endpoint` | Card to manifest: must be a `wss` URL |
| `capabilities.asap_version` | Extension `asap_version` | |
| `capabilities.state_persistence` | Extension `state_persistence` | Without the extension: `false` |
| `capabilities.streaming` | `capabilities.streaming` | Always written. An absent value reads as `false` |
| `capabilities.skills[].id` | `skills[].id` | Skill ids must be unique |
| `capabilities.skills[].description` | `skills[].description` | |
| `capabilities.skills[].input_schema`, `output_schema` | Extension `skill_schemas.<skill id>` | |

A card needs data that a manifest does not hold. You give it in `AgentCardInputs`:

| Card field | `AgentCardInputs` field | Required |
|------------|-------------------------|----------|
| `supportedInterfaces` | `interfaces` | Yes, at least one |
| `defaultInputModes`, `defaultOutputModes` | `default_input_modes`, `default_output_modes` | Yes, at least one each |
| `skills[].tags` | `skill_tags` | Yes, at least one tag for every skill |
| `skills[].name` | `skill_names` | No. The default is the skill `id` |
| `provider`, `documentationUrl`, `iconUrl` | `provider`, `documentation_url`, `icon_url` | No |
| `securitySchemes`, `securityRequirements`, `skills[].securityRequirements` | `security_schemes`, `security_requirements`, `skill_security_requirements`, or the `external_tokens` argument of `build_signed_agent_card` | No |

### Manifest fields that a card does not carry

| Manifest field | Reason |
|----------------|--------|
| `supported_versions` | ASAP HTTP wire versions of the ASAP endpoint. Each A2A interface has its own `protocolVersion`. Not in the extension params |
| `auth` | Configures the ASAP endpoint. The card's security schemes describe the A2A interfaces and come from caller input |
| `signature` | Signs the manifest, not the card. Agent Card signatures are separate |
| `sla` | No A2A v1.0 field, and not in the extension params |
| `verification` | A registry trust badge. A card must not assert it about itself |
| `ttl_seconds` | No A2A v1.0 field. A2A uses HTTP caching headers for the card |
| `capabilities.mcp_tools` | No A2A v1.0 field, and not in the extension params |
| `capabilities.hardware` | No A2A v1.0 field, and not in the extension params |
| `capabilities.inference` | No A2A v1.0 field, and not in the extension params |

### Card fields that a manifest does not carry

| Card field | Reason |
|------------|--------|
| `supportedInterfaces` | A2A endpoints. The manifest endpoint is the ASAP endpoint, from the extension or from you |
| `provider`, `documentationUrl`, `iconUrl` | No manifest field |
| `capabilities.pushNotifications`, `capabilities.extendedAgentCard` | No manifest field |
| `capabilities.extensions[]` other than the ASAP extension | No manifest field |
| `securitySchemes`, `securityRequirements`, `skills[].securityRequirements` | They describe the A2A interfaces, not the ASAP endpoint, and a card signature does not cover all of them |
| `defaultInputModes`, `defaultOutputModes`, `skills[].inputModes`, `skills[].outputModes` | No manifest field. ASAP skills use JSON Schema |
| `skills[].name` | No manifest field. Listed only when it differs from the skill `id`, which the mapping uses as the name |
| `skills[].tags`, `skills[].examples` | No manifest field |
| `signatures` | They cover the card, not a manifest. Verify them before you map the card. The manifest signature is a different scheme |

### The ASAP extension

ASAP data that a card has no field for rides in one A2A extension, in `capabilities.extensions[]`. A card signature covers it.

- **URI:** `https://asap-protocol.com/a2a/extensions/manifest/v1`. The page at that address describes the params.
- **`required`:** `false`, so an A2A client that does not know ASAP can still use the card.
- A reader ignores params that it does not know. A breaking change to the params will get a new URI.

| Param | Type | Required |
|-------|------|----------|
| `agent_urn` | string | Yes |
| `asap_endpoint` | string | Yes |
| `events_endpoint` | string | No |
| `asap_version` | string | Yes |
| `state_persistence` | boolean | No (default `false`) |
| `skill_schemas` | object: skill `id` → `{input_schema, output_schema}`, only for skills that have a schema | No |

### Mapping rules

- **Security schemes come only from you.** The card's `securitySchemes` and `securityRequirements` come from `AgentCardInputs` or from the token config. The manifest's `auth` is not mapped in either direction: it configures the ASAP endpoint, while the card's schemes describe the A2A interfaces.
- **`securityRequirements` is a list of alternatives.** A client may meet any one entry. Inside one entry, it must meet every named scheme, as in OpenAPI. A2A v1.0 does not say this; it is how ASAP reads the list.
- **No requirement without a scope.** The mapping refuses a requirement that names no scheme, a scheme with no scope, or a scheme that the card does not declare. A signature does not cover such an entry, so it is never written.
- **Skill schemas with empty values are refused, not changed.** A schema that holds `"required": []`, `"properties": {}`, `"enum": []`, or `"default": null` makes signing fail with `A2ACardNotSignableError`, whose `paths` name every such value. The signed form of a card drops empty values, so they would be data that no signature covers. Fix the schema at its source.
- **A2A v0.3 cards are refused** with `A2AUnsupportedVersionError`. A v0.3 card has a top-level `url` or `protocolVersion` and no `supportedInterfaces`.
- A card lists at most 1000 skills.

## Signed Agent Cards

An A2A v1.0 card can carry signatures in `signatures[]`. Each one is a JWS with a detached payload: the signed bytes are the RFC 8785 (JCS) form of the card without `signatures`, after empty values are removed, as the A2A SDKs do.

### Signing

- **Default:** one `EdDSA` signature with the agent's Ed25519 key.
- **Extra `ES256` signature (opt-in):** a verifier that allows only `ES256` and `RS256` refuses a card that has only an `EdDSA` signature. To serve such verifiers too, add a second signer with a P-256 key. A verifier skips a signature whose algorithm it does not allow, so the card then passes in both kinds of verifier.

```python
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.adapters.a2a import CardSigner

signers = [
    CardSigner(key=Ed25519PrivateKey.generate(), kid="research-card-1"),
    CardSigner(key=generate_private_key(SECP256R1()), kid="research-card-ec-1", alg="ES256"),
]
```

- The protected header holds `alg`, `kid`, and `typ: "JOSE"`, plus `jku` when you set it on the signer.
- The signer refuses a card whose signed form would lose data: an empty string, list, or object, a `null`, or an integer beyond ±(2^53 − 1). The error lists every such path.
- `build_signed_agent_card` needs at least one signer. A card that cannot be signed is never built, so there is no unsigned card to serve.

### Verification policy

- **A signature is required by default.** An unsigned card fails with `A2AUnsignedCardError`. `A2ATrustConfig(policy="allow_unsigned")` accepts it, and then `result.verified` is `false`: the whole manifest is unverified data.
- **A bad signature always fails.** A signature from a trusted key that does not verify fails the card, even when another signature verifies. The A2A Python SDK accepts the first signature that verifies; ASAP is stricter.
- **An unknown key is skipped.** A signature whose `kid` you do not trust, or whose algorithm is off your list, is skipped. A card whose signatures are all skipped fails with `A2ANoTrustedSignatureError`. So a card can carry an old and a new key during a key rotation.
- **A malformed signature entry fails the card**, before any key lookup. A card carries at most 16 signatures.
- Every failure is a subclass of `A2ACardVerificationError`. No error message holds key material.

### Trust store and `jku` allowlist

`A2ATrustConfig` says which keys may sign the cards that you ingest, and for which agents.

```python
from asap.adapters.a2a import ANY_AGENT, A2ATrustConfig

trust = A2ATrustConfig.model_validate({
    "pinned_keys": {
        "bank-a-card-1": {
            "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"},
            "agents": ["urn:asap:agent:bank-a"],
        }
    },
    "jku_allowlist": {
        "https://keys.bank-b.example/jwks.json": {
            "agents": ["urn:asap:agent:bank-b", "urn:asap:agent:bank-b:payments"],
        },
        "https://registry.example/jwks.json": {"agents": ANY_AGENT},
    },
})
```

- **Every trust source names its agents.** A signature proves who signed the card, not whose card it is. So a card passes only when a key that verified may sign for the card's agent URN; otherwise ingest raises `A2ASignerScopeError`. `agents` is required. The value `ANY_AGENT` lets a source sign for every agent: whoever holds that key, or controls that `jku` host, can then make a card that verifies for any agent. Use it only for a source that you trust that much, such as your own registry key.
- **Pinned keys come first.** A pinned `kid` is used at once, and nothing is fetched.
- **A `jku` is fetched only when it equals an allowlist entry exactly**, character for character. A `jku` that is not on the allowlist causes no request. The fetch takes HTTPS only, refuses private and loopback addresses, follows no redirect, and reads at most 64 KiB. A fetched key set is kept for 300 seconds; an unknown `kid` does not cause a new fetch before then.
- Pinned keys must be public OKP, EC, or RSA keys. A small-order Ed25519 key is refused.
- `allow_private_hosts=True` lets entries and fetches reach private and loopback hosts. It is for local development and tests only.

### Algorithms

| Algorithm | Key | Sign | Verify |
|-----------|-----|------|--------|
| `EdDSA` | Ed25519 (never Ed448) | Yes, the default | Yes |
| `Ed25519` (the RFC 9864 name) | Ed25519 | No: the A2A Python SDK cannot verify this name | Yes |
| `ES256` | P-256 | Yes, opt-in | Yes |
| `RS256` | RSA, 2048 bits or more | No | Yes |

`none` and every `HS*` algorithm are always refused, before any key lookup. The algorithm in the header must fit the key: an `ES256` signature from an RSA key fails.

### What "verified" means

When `result.verified` is `true` after `AgentCardIngestor.ingest`, or after `verify_agent_card_for_agent`:

- a signature over the card verified with a key that you trust;
- no signature from a trusted key failed;
- the signature covers every value of the ASAP extension params;
- a key that verified may sign for the card's agent URN.

It does **not** mean:

- **that the card came from the URL that served it.** The URL is not signed. A copy of the card verifies from any URL.
- **that the card is fresh.** A signed card has no expiry and no nonce. It can be served again for as long as its key stays trusted.
- **more than the weakest key.** A card for an agent is as strong as the weakest trusted key that may sign for that agent, an `ANY_AGENT` source included.

`verify_agent_card` alone checks the signatures but does not bind the card to an agent. Call `verify_agent_card_for_agent`, or `CardVerification.require_signer_for(agent_urn)`, or use `AgentCardIngestor`, which does both.

## Related documentation

- [MCP Auth Bridge](../adapters/mcp-auth-bridge.md): the Python stdio bridge and `@asap-protocol/mcp-auth` for HTTP servers
- [Support tiers](../integrations/support-tiers.md)
- [Migration: upgrading from v2.5.x to v2.6.0](../migration.md#upgrading-from-v25x-to-v260)
- [ASAP extension for A2A](https://asap-protocol.com/a2a/extensions/manifest/v1): the params of the extension
- [A2A gateway example](https://github.com/asap-protocol/asap-protocol/tree/main/examples/a2a_gateway)
