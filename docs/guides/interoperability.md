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
| An OAuth 2.1 authorization server | ASAP grants for its access tokens on a Python ASAP server | [External access tokens](#external-access-tokens-on-an-asap-server-python) |
| An HTTP MCP server in TypeScript | Agent JWTs or access tokens checked against grants | [Flow B](#flow-b-an-mcp-client-calls-an-http-mcp-server) |
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

## External access tokens on an ASAP server (Python)

An ASAP server can accept OAuth 2.1 access tokens from issuers that you list, and give each token only the grants that your rules map. The reference gateway of Flow A uses this verifier. `create_app` uses it on one route: `POST /asap/capability/execute`.

### What a principal is

For each request, ASAP verifies the token and builds an `ExternalPrincipal`: the issuer, the subject (`sub`), the client id, the scopes, the expiry, and the grants that your rules give. It is keyed by `(issuer, subject)`.

- It lives for that request only. Nothing is stored, and the next request is checked again.
- It is not an ASAP agent. It has no host, no key, no registry entry, and no `agent_id`, and it never reads the grants of the capability registry. Its grants come only from the rules of its issuer.
- This is the OAuth *resource server* model: the token comes with every request, and the server checks it every time.

### Configure the issuers

```python
from asap.auth.external_tokens import ExternalTokenConfig
from asap.transport.server import create_app

tokens = ExternalTokenConfig.model_validate({
    "issuers": [
        {
            "issuer": "https://auth.example.com",
            "audience": "https://agent.example.com/asap",
            "key_source": {"kind": "jwks_uri", "jwks_uri": "https://auth.example.com/.well-known/jwks.json"},
            "grant_rules": [
                {"scope": "files:read", "capability": "file:read"},
                {"scope": "files:write", "capability": "file:write", "constraints": {"path": {"in": ["/tmp"]}}},
                {"claim": "groups", "value": "auditors", "capability": "file:read"},
            ],
        }
    ]
})
app = create_app(manifest, external_token_config=tokens)
```

Each issuer entry has these fields:

| Field | Meaning |
|-------|---------|
| `issuer` | The issuer identifier. The token's `iss` must match it (a trailing slash is ignored). A token from an issuer that is not listed is refused. There is no "accept any issuer" mode |
| `audience` | Required. The identifier of this server (RFC 8707). The token's `aud` must name it. A token for another resource is refused and never passed on |
| `key_source` | Where the issuer's public keys come from: `{"kind": "jwks_uri", "jwks_uri": "https://..."}`, `{"kind": "oidc_discovery"}` (reads `jwks_uri` from `<issuer>/.well-known/openid-configuration`), or `{"kind": "static_jwks", "jwks": {...}}` (no fetch) |
| `introspection` | For opaque tokens, instead of `key_source`: see [Opaque tokens](#opaque-tokens) |
| `grant_rules` | The only source of grants for this issuer's tokens. No rule, no grant |
| `required_claims` | Claims that every token must hold, with exact values. See [With `oauth2_config`](#with-oauth2_config) |
| `allow_private_hosts` | Lets the URLs of this entry reach private or loopback hosts. For local development only |

The config refuses an empty issuer list, a missing audience, a duplicate issuer, a URL that is not HTTPS or that names `localhost` or a private IP address, and a static JWKS with a private or symmetric key.

How a token is checked, in this order:

1. The header: an ASAP Agent JWT (`typ: agent+jwt`) or Host JWT (`typ: host+jwt`) is refused. So are `alg: none`, every `HS*` algorithm, and any other algorithm that is not RSA, RSA-PSS, ECDSA, or `EdDSA`.
2. The issuer: the token's `iss` must be listed.
3. The signature, with that issuer's keys only. A `jku`, `x5u`, or `jwk` header in the token is ignored.
4. The claims: `aud` must name the audience; `exp` is required and `nbf` is checked, with 30 seconds of clock skew; `sub` must be a non-empty string; then `required_claims`.
5. The grants, from the rules.

Every fetch (JWKS, OpenID discovery, introspection) takes HTTPS only, refuses private addresses, and follows no redirect. A fetched key set is kept for 300 seconds. An unknown `kid` fetches the set again, at most once every 30 seconds per issuer.

### Grant rules

A rule gives one capability, with optional constraints, when it matches the token:

- **`scope` rule:** matches one entry of the `scope` claim (a space-separated string). A token with `scope: "files:read files:write"` matches both scope rules above.
- **`claim` rule:** matches when a top-level claim is exactly `value`, or is a list that holds `value`. Numbers, booleans, and nested objects never match. The `groups` rule above matches `"groups": ["auditors", "dev"]`.

Deny by default:

- A scope or a claim that no rule names gives nothing. A broad scope such as `admin` gives nothing unless a rule names it.
- Each capability gets at most one grant. Two rules that give one capability must have the same constraints, or the config is refused.
- A constraint object may hold only `max`, `min`, `in`, and `not_in`. `max` and `min` need a finite number; `in` and `not_in` need a list.
- At call time, a constrained field that the call leaves out is a violation, as is a check that cannot be evaluated, such as `NaN` against `max`.

### Call the route

`POST /asap/capability/execute` takes the capability name and its arguments. With the config above:

```http
POST /asap/capability/execute
Authorization: Bearer <access token with scope "files:read">
Content-Type: application/json

{"capability": "file:read"}
```

```http
HTTP/1.1 200 OK

{"capability": "file:read", "status": "executed", "principal": {"type": "external", "issuer": "https://auth.example.com", "subject": "user-42"}}
```

- The answer holds the principal, never an `agent_id`.
- The route checks the token and the grant, and answers. It does not run code for the capability.
- The route still accepts an ASAP Agent JWT, exactly as before. The header `typ` picks the path: `agent+jwt` goes to the Agent JWT check, and every other bearer goes to the access-token check. A token that one check refuses is never tried with the other.
- Only this route accepts access tokens. `GET /asap/capability/list` and `describe`, JSON-RPC `POST /asap`, the WebSocket, and `/asap/agent/*` keep their own authentication.
- Each call is logged as `asap.capability.external_execute`, with the issuer, the subject, the client id, the capability, and the outcome. No answer and no log line holds the token.

| Case | Answer |
|------|--------|
| No bearer | 401 `{"detail": "Authentication required"}`, `WWW-Authenticate: Bearer` |
| Token refused, for any reason | 401 `{"detail": "invalid_token", "request_id": "…"}`, `WWW-Authenticate: Bearer error="invalid_token"`. The reason goes to the log only |
| The issuer's keys or the introspection endpoint cannot be reached | 503 `{"detail": "Authentication service unavailable", "request_id": "…"}` |
| No grant for the capability | 403, `error.code` `capability_not_granted`, `WWW-Authenticate: Bearer error="insufficient_scope"` |
| A constraint is broken | 403, `error` `constraint_violated` with the `violations` (field, operator, message), no challenge |
| A body that is not valid JSON or not a valid request | 400, after the token check |

ASAP also adds its own `ASAP` challenge to each 401 under `/asap/capability`. A 503 shows that the issuer that the token names is listed: a client can tell an outage from a bad token. The issuer list is not a secret.

### With `oauth2_config`

`oauth2_config` puts `OAuth2Middleware` in front of `/asap`. `POST /asap/capability/execute` always verifies its own bearer, so the middleware skips that exact path. Any other spelling of the path still goes through the middleware.

To accept the tokens of the `oauth2_config` identity provider on execute, list it as an issuer, with its audience and grant rules. The middleware does not run on that route, so its checks do not apply there: `required_scope`, the identity binding of the agent (the custom claim or the `ASAP_AUTH_SUBJECT_MAP` allowlist), and its expected issuer and audience. The entry's own `issuer`, `audience`, and grant rules apply instead.

One provider can mint tokens for many agents with one audience. To keep a token for agent X off agent Y, set `required_claims` to the middleware's custom claim and this agent's URN:

```python
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM

idp_issuer = {
    "issuer": "https://idp.example.com",
    "audience": "https://agent.example.com/asap",
    "key_source": {"kind": "jwks_uri", "jwks_uri": "https://idp.example.com/.well-known/jwks.json"},
    "grant_rules": [{"scope": "files:read", "capability": "file:read"}],
    "required_claims": {DEFAULT_CUSTOM_CLAIM: "urn:asap:agent:research"},  # the claim and this agent's URN
}
```

Use the claim name that the middleware uses: `oauth2_config.custom_claim`, else the `ASAP_AUTH_CUSTOM_CLAIM` environment variable, else `DEFAULT_CUSTOM_CLAIM`. A token whose claim is missing or different gets the `invalid_token` 401. Without `required_claims` on that entry, `create_app` logs the warning `asap.server.external_issuer_unbound` with the value to set. The subject allowlist has no equivalent.

### Opaque tokens

For an issuer whose tokens are not JWTs, set `introspection` instead of `key_source`:

```python
opaque_token_issuer = {
    "issuer": "https://auth.example.com",
    "audience": "https://agent.example.com/asap",
    "introspection": {
        "url": "https://auth.example.com/oauth2/introspect",
        "client_id": "asap-agent",
        "client_secret": "<secret>",
        "cache_ttl_seconds": 60,
    },
    "grant_rules": [{"scope": "files:read", "capability": "file:read"}],
}
```

- At most one issuer may use introspection. An opaque token names no issuer, so it goes to that one endpoint, with this server's client credentials (HTTP Basic).
- A bearer shaped like a JWT is never sent to the endpoint unless its `iss` is that issuer.
- The answer must say `active: true`. Its claims then get the same checks as a JWT: `aud`, `exp`, `sub`, `required_claims`, and the grant rules.
- `cache_ttl_seconds` (default 60, at most 3600) bounds how long an answer is reused. A token revoked at the issuer stops working within that time. `0` asks the endpoint on every request.

## Flow B: an MCP client calls an HTTP MCP server

`@asap-protocol/mcp-auth` protects an MCP server in TypeScript that serves Streamable HTTP on `@modelcontextprotocol/server` 2.x, MCP revision `2026-07-28` only. It accepts two kinds of token, and they differ in how they relate to MCP:

| Token | Sent by | Relation to MCP authorization (OAuth 2.1) |
|-------|---------|-------------------------------------------|
| OAuth 2.1 access token (a JWT) | A standard MCP client, after it signs in at your authorization server | Your server is the resource-server side of MCP authorization |
| ASAP Agent JWT | An ASAP agent, a new token for each request | None. It is a custom auth strategy, which MCP allows. It is not MCP authorization |

### Who trusts whom

```text
MCP client                                   HTTP MCP server with @asap-protocol/mcp-auth
    │                                                    │
    │ POST /mcp  tools/call                              │
    │ Authorization: Bearer <access token or Agent JWT>  │
    │ ─────────────────────────────────────────────────► │ token layer: verify the token
    │                                                    │   no token or a bad token: HTTP 401
    │                                                    │   a tool or method you did not declare: HTTP 403
    │                                                    │ grant layer: grant and constraints for this tool
    │ ◄───────────────────────────────────── tool result │   refused: an error tool result (or HTTP 403)
```

- **Access token:** the server trusts the issuers that you list, and only tokens whose audience is the MCP URL. It never passes the token on. It links its RFC 9728 protected-resource metadata from every challenge, so a client can find the authorization server. You serve that document; the package does not.
- **Agent JWT:** the server trusts the agents that your resolver returns, with their Ed25519 keys and their host. Each Agent JWT works once and lives 60 seconds.
- Grants come from your config: a map for agents, or grant rules for access tokens, deny by default.

### Run the example

[`examples/mcp_auth_http/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/mcp_auth_http) is a server with one public tool, `echo`, and one protected tool, `secure_action`. It creates a demo agent and a local test issuer at each start. From the repository root:

```bash
pnpm install
pnpm --filter @asap-protocol/client run build && pnpm --filter @asap-protocol/mcp-auth run build
pnpm --filter example-mcp-auth-http test
```

The tests start the server on a free local port and run the five cases of the flow with the MCP SDK client: a granted call, no token, a bad token, a call with no grant, and a broken constraint. They also call with an access token twice, and with an access token that lacks the scope.

To run the server and call the public tool with plain HTTP:

```bash
pnpm --filter example-mcp-auth-http start   # MCP endpoint: http://127.0.0.1:8787/mcp
```

```bash
curl -s http://127.0.0.1:8787/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: echo' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hi"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

The answer:

```json
{"result":{"content":[{"type":"text","text":"hi"}],"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"mcp-auth-http-example","version":"0.1.0"}}},"jsonrpc":"2.0","id":1}
```

- Revision `2026-07-28` has no `initialize` handshake and no session: one POST calls one tool.
- The headers must agree with the body. Both `_meta` keys are required.
- A request with no token passes only with `MCP-Protocol-Version: 2026-07-28`, and only for a public tool, `tools/list`, or `server/discover`. A call to `secure_action` needs `Authorization: Bearer <token>`.

### Write your own server

[MCP Auth Bridge: HTTP MCP servers in TypeScript](../adapters/mcp-auth-bridge.md#http-mcp-servers-in-typescript) has the code for a server with Agent JWTs, a server with access tokens, a client for each, and every answer of the package. The parts that matter for interoperability:

- `createMcpAuthMiddleware({ externalTokens: { issuers: [...] }, resourceMetadataUrl, ... })` takes the same kind of issuer entry as the Python config: `issuer`, a required `audience`, keys from `jwksUri` or a static `jwks`, and `grantRules` with `scope` or `claim` rules. The rules and the constraint checks give the same answers as Python: both run the shared test vectors in `tests/fixtures/interop/`.
- The package accepts access tokens signed with `EdDSA`, `RS256`, or `ES256`. The Python path also accepts `RS384`, `RS512`, the `PS*` algorithms, `ES384`, and `ES512`.
- With `externalTokens`, `resourceMetadataUrl` is required, and you serve the metadata document at that URL.
- `toolCapabilities` must list every protected tool, and every one must be wrapped with `protectTool`. A tool or a method that the config does not declare gets HTTP 403 for a verified token.
- `scopeChallenge` on a protected tool turns a missing grant for an access token into HTTP 403 `insufficient_scope`, with the scope that gives the grant.
- With Agent JWTs, `verifyAgentJwt` from `@asap-protocol/client` 2.5.0 checks each token. Your `resolveAgent` callback returns the agent's key and whether the agent is usable.

## The four `asap:*` codes over HTTP

ASAP reports a refusal with one of four codes: `asap:auth_required`, `asap:invalid_token`, `asap:capability_denied`, and `asap:constraint_violation`. The Python stdio bridge returns every one of them in a tool result, because stdio has no HTTP layer. Over HTTP, each path puts them where its protocol expects them:

| Case | Flow A gateway (A2A) | Python `POST /asap/capability/execute`, access token | `@asap-protocol/mcp-auth`, access token | `@asap-protocol/mcp-auth`, Agent JWT |
|------|----------------------|------------------------------------------------------|-----------------------------------------|--------------------------------------|
| No token | HTTP 401, `asap:auth_required` | HTTP 401, `Authentication required` | HTTP 401, `asap:auth_required` | HTTP 401, `asap:auth_required` |
| Token refused | HTTP 401, `asap:invalid_token` | HTTP 401, `invalid_token` | HTTP 401, `asap:invalid_token: <reason>` | HTTP 401, `asap:invalid_token: <reason>` |
| The issuer's keys cannot be fetched | HTTP 503 | HTTP 503 | HTTP 503, with `Retry-After` | Does not apply |
| No grant for the skill, tool, or capability | HTTP 403 `insufficient_scope`, `asap:capability_denied` | HTTP 403 `insufficient_scope`, `capability_not_granted` | HTTP 403 `insufficient_scope`, `asap:capability_denied`, when a scope rule gives the grant and the tool has `scopeChallenge`; else a tool result | Tool result, `asap:capability_denied` |
| A constraint is broken | HTTP 403, `asap:constraint_violation`, no challenge | HTTP 403, `constraint_violated`, no challenge | Tool result, `asap:constraint_violation` | Tool result, `asap:constraint_violation` |

Why the paths differ:

- **Token errors are HTTP 401 everywhere.** MCP authorization requires a 401 for a missing or invalid token, and a tool result cannot carry an HTTP status.
- **A missing grant on an access-token path is HTTP 403 `insufficient_scope`.** A standard OAuth client reads this challenge and can ask for more scope. On the Agent JWT path of `mcp-auth`, a missing grant stays a tool result, as in the Python bridge.
- **A broken constraint is a tool result in MCP and HTTP 403 elsewhere.** It depends on the arguments, and more scope does not help, so its 403 has no challenge. MCP has a tool result to carry it; A2A and the Python route do not.
- The Python execute route answers with its own error bodies, not the `asap:*` strings. The status codes and the challenges follow the same rules.

## Compliance checks

The `mcp-auth-http` profile of `asap-compliance` checks Flow B against a running server over plain HTTP. It needs no MCP SDK. It gets a fresh token for each request from the server's test-only probe endpoint, so a second run passes too. It checks each answer for both the code and the layer:

| Check | Expected answer |
|-------|-----------------|
| `public_tool` | No token, public tool: a tool result |
| `auth_required` | No token, protected tool: HTTP 401, `asap:auth_required` |
| `invalid_token`, `bad_signature` | A malformed token, and an Agent JWT with a bad signature: HTTP 401, `asap:invalid_token` |
| `valid_jwt` | An Agent JWT with the grant: a tool result |
| `replayed_jwt` | The same Agent JWT twice: the second gets HTTP 401 |
| `wrong_capability`, `constraint_violation` | Agent JWT: a tool result with `asap:capability_denied`, then `asap:constraint_violation` |
| `external_valid_token` | The same access token twice: both work |
| `external_wrong_capability` | An access token with no grant: HTTP 403, `asap:capability_denied` |
| `external_constraint_violation` | An access token, arguments that break the constraint: a tool result with `asap:constraint_violation` |
| `undeclared_tool` | An Agent JWT for a tool that the server does not declare: HTTP 403 |
| `www_authenticate_on_401` | Every 401 has a `Bearer` challenge |
| `token_not_echoed` | No answer holds a token that the run sent |

[Compliance testing](compliance-testing.md) shows how to install and run the profiles. The probe endpoint hands out valid tokens to anyone who can reach the server: turn it on only for a local or CI run (`pnpm --filter example-mcp-auth-http start -- --compliance`).

## Security notes

- **No token passthrough.** A server never sends on the token that it received. Every path checks the audience, so a token for another resource is refused. The gateway also removes `Authorization` and `Cookie` before the A2A SDK and the skills see the request.
- **An audience on every token.** The audience is required config on every path. No mode skips it, and no mode accepts any issuer.
- **No replay check on access tokens.** An OAuth client sends the same access token on every request until it expires, and MCP requires the token on every request. Refusing a second use would break every standard client. An access token works until its `exp`. For an opaque token, a revocation at the issuer takes effect within the introspection cache time.
- **Agent JWTs work once.** A replay cache keeps the `jti` of each accepted Agent JWT and refuses it a second time. A token whose `exp` is more than 120 seconds after the current time, or after its `iat`, is refused, so a token cannot outlive its replay record. The default replay cache of `@asap-protocol/mcp-auth` is in memory, in one process: several server instances with one audience need a shared cache.
- **Token types are kept apart.** The header `typ` picks the check: `agent+jwt` for an Agent JWT, any other token for the access-token check. A token that one check refuses is never tried with the other, and the access-token check refuses an ASAP Agent JWT or Host JWT.
- **Fetches are guarded.** In Python, every fetch of a card, a key set, OpenID discovery, or introspection takes HTTPS only, resolves the host once and refuses private, loopback, and link-local addresses, follows no redirect, and stops at a size and time limit. In Node.js, `@asap-protocol/mcp-auth` cannot check the address behind a host name; see [Key fetch and SSRF](../adapters/mcp-auth-bridge.md#key-fetch-and-ssrf).
- **No token in answers or logs.** Refusals carry a code and a fixed reason. The logs hold the issuer, the subject, and the reason, never the token.

## What is not supported

- **Replacing A2A or MCP.** ASAP layers on them.
- **An A2A server or client in `asap-protocol`.** Use the A2A SDK. The gateway in `examples/a2a_gateway/` is an example.
- **The ASAP envelope in these flows.** Both flows use A2A and MCP on their own wire formats.
- **Outbound A2A calls.** `asap-protocol` fetches, verifies, and maps a card. It does not call an A2A agent.
- **A2A v0.3 Agent Cards.** They are refused with `A2AUnsupportedVersionError`.
- **HTTP MCP servers in Python.** The Python bridge covers stdio only.
- **Opaque access tokens in `@asap-protocol/mcp-auth`.** It accepts JWT access tokens only. The Python path accepts opaque tokens through introspection.
- **MCP TypeScript SDK 1.x (`@modelcontextprotocol/sdk`), the HTTP+SSE transport, and MCP revisions before `2026-07-28`** in `@asap-protocol/mcp-auth`.
- **Access tokens on other ASAP routes.** Only `POST /asap/capability/execute` accepts them.
- **RFC 9728 metadata from the Python ASAP server.** An A2A client finds the issuer in the card's security scheme.
- **Approval flows as a source of grants for access tokens.** Grants come only from static rules.
- **Hiding tools from `tools/list`.** Every caller sees every tool.

## IETF drafts that the project tracks

This section is for information. No behavior in this release depends on these drafts.

- `draft-ietf-oauth-v2-1`: OAuth 2.1, which MCP authorization refers to
- `draft-ietf-wimse-aims`, from the IETF WIMSE working group
- `draft-ietf-oauth-identity-assertion-authz-grant`
- `draft-ietf-oauth-client-id-metadata-document`

## Related documentation

- [MCP Auth Bridge](../adapters/mcp-auth-bridge.md): the Python stdio bridge and `@asap-protocol/mcp-auth` for HTTP servers
- [TypeScript client](../sdks/typescript.md): Agent JWT minting and `verifyAgentJwt`
- [Compliance testing](compliance-testing.md)
- [Support tiers](../integrations/support-tiers.md)
- [Migration: upgrading from v2.5.x to v2.6.0](../migration.md#upgrading-from-v25x-to-v260)
- [ASAP extension for A2A](https://asap-protocol.com/a2a/extensions/manifest/v1): the params of the extension
- [A2A gateway example](https://github.com/asap-protocol/asap-protocol/tree/main/examples/a2a_gateway) and [HTTP MCP example](https://github.com/asap-protocol/asap-protocol/tree/main/examples/mcp_auth_http)
