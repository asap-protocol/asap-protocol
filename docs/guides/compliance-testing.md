# Compliance Testing (v1.2)

The **asap-compliance** package validates that ASAP agents follow the protocol specification. Use it to certify your agents are ASAP-compliant and interoperable.

## Installation

```bash
uv add asap-compliance
# or
pip install asap-compliance
```

## Quick Start

Run compliance tests against a running agent:

```bash
pytest --asap-agent-url https://your-agent.example.com -m asap_compliance
```

Or set the URL via environment variable:

```bash
ASAP_AGENT_URL=https://your-agent.example.com pytest -m asap_compliance
```

## Test Categories

| Category | What it validates |
|----------|------------------|
| **handshake** | Agent reachable, health endpoint (200, application/json), manifest schema, signed manifest verification, version compatibility |
| **schema** | Envelope, TaskRequest, TaskResponse, McpToolResult, MessageAck; `extra="forbid"` compliance |
| **state** | Task state machine transitions (PENDING → RUNNING → COMPLETED/FAILED) |
| **sla** | Task completes within timeout; progress schema valid (optional, requires echo skill) |

## Programmatic Usage

### Handshake validation (against live agent)

```python
from asap_compliance import ComplianceConfig, validate_handshake

config = ComplianceConfig(agent_url="https://your-agent.example.com")
result = validate_handshake(config)
if result.passed:
    print("Agent is compliant")
else:
    for check in result.checks:
        if not check.passed:
            print(f"FAIL: {check.name} - {check.message}")
```

### Schema validation (static, for envelope/payload dicts)

```python
from asap_compliance import validate_schema

envelope_dict = {
    "asap_version": "0.1",
    "sender": "urn:asap:agent:a",
    "recipient": "urn:asap:agent:b",
    "payload_type": "task.request",
    "payload": {"conversation_id": "conv-1", "skill_id": "echo", "input": {}},
}
result = validate_schema(envelope_dict)
if result.passed:
    print("Envelope and payload schema valid")
```

### State machine validation

```python
from asap_compliance import ComplianceConfig, validate_state_machine_async

config = ComplianceConfig(
    agent_url="https://your-agent.example.com",
    sla_skill_id="echo",  # Agent must implement this skill
)
result = await validate_state_machine_async(config)
if result.passed:
    print("State transitions correct")
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `agent_url` | Required | Base URL of the agent under test |
| `timeout_seconds` | 30.0 | HTTP timeout |
| `test_categories` | handshake, schema, state | Categories to run |
| `sla_skill_id` | echo | Skill ID for SLA and state validation |
| `skip_checks` | [] | Check names to skip (e.g. `sla`) |

## Requirements for Compliance

1. **Health endpoint**: `GET /.well-known/asap/health` returns 200 with `application/json`
2. **Manifest endpoint**: `GET /.well-known/asap/manifest.json` returns valid manifest (plain or signed)
3. **ASAP endpoint**: `POST /asap` accepts JSON-RPC with envelope, returns valid response
4. **Echo skill**: For full pipeline, agent must implement `echo` skill (or set `sla_skill_id` to match)
5. **Schema**: Envelope and payloads must pass Pydantic validation with `extra="forbid"`

## Signed Manifests

The handshake validator accepts both plain and signed manifests. If the agent returns a signed manifest (v1.2), the harness verifies the Ed25519 signature before proceeding.

## Profiles

A profile checks one integration against a running server. It returns one result per check ID. A check ID is also the key that skips the check (`skip_checks`).

| Profile | Server under test | How the profile talks to it |
|---------|-------------------|-----------------------------|
| `mcp-auth-bridge` | A stdio MCP server protected by `asap.mcp.auth.protect_server` | A subprocess that the profile starts. See the [MCP Auth Bridge example](https://github.com/asap-protocol/asap-protocol/blob/main/examples/mcp_auth_bridge/README.md#compliance) |
| `mcp-auth-http` | An HTTP MCP server protected by `@asap-protocol/mcp-auth` | Plain HTTP: one `tools/call` POST per request, MCP revision `2026-07-28`, no MCP SDK |
| `a2a-bridge` | An A2A v1.0 agent served by an ASAP gateway | Plain HTTP: the Agent Card, the ASAP manifest, and one A2A `SendMessage` per request |

The two HTTP profiles need `asap-protocol` 2.6.0 or later. [Interoperability](interoperability.md) describes the two flows that they check.

### Run a profile

Put the config fields of the profile in a JSON file. Each profile below lists its fields. Then run the profile with Python or with pytest.

With Python, this script prints one line per check, and exits with 1 when a check fails:

```python
# run_profile.py
import json
import sys

from asap_compliance import profile_config, validate_profile

name, config_file = sys.argv[1], sys.argv[2]
with open(config_file, encoding="utf-8") as fields:
    result = validate_profile(profile_config(name, json.load(fields)))
for check in result.checks:
    print("PASS" if check.passed else "FAIL", check.name, "-", check.message)
sys.exit(0 if result.passed else 1)
```

```bash
python run_profile.py mcp-auth-http mcp-auth-http.json
```

With pytest, the plugin of `asap-compliance` adds the options `--asap-profile` and `--asap-profile-config`. Its fixture `compliance_profile` holds the config of the selected profile:

```python
# test_profile.py
from asap_compliance import validate_profile


def test_profile(compliance_profile):
    result = validate_profile(compliance_profile)
    assert result.passed, [check for check in result.checks if not check.passed]
```

```bash
pytest test_profile.py --asap-profile mcp-auth-http --asap-profile-config mcp-auth-http.json
```

An unknown profile name fails, with the list of the known names: `profile_config` raises `ValueError`, and pytest stops with a usage error. Each profile also has its own entry points: `validate_mcp_auth_http` and `validate_a2a_bridge`, and their `_async` forms.

### A failing check

A failed check says what it expected, and then what it got. It quotes no server text other than an `asap:*` code or a JSON-RPC error code, and it never holds a token or a key. The other checks still run. This is a run of `a2a-bridge` with a config that expects another agent URN (two of its 16 lines):

```text
FAIL card_signature - expected a signature by a configured key, none that fails, and a signer that may sign for the agent that the card names; a configured key verified the card, but the card names another agent, and that key may sign for urn:asap:agent:someone-else only
FAIL card_agent_urn - expected an ASAP extension in the card that names the expected agent URN; the card's ASAP extension names an agent URN other than urn:asap:agent:someone-else
```

With pytest, the test fails, and the assertion lists the failed checks.

### Probe tokens

An ASAP Agent JWT works once and lives 60 seconds, so a token in a config file fails on the next run. A profile asks for a fresh token just before each request that needs one. It never sends a token twice, except where a check does so on purpose (`replayed_jwt`, `external_valid_token`).

By default, the tokens come from `probe_tokens_url`: for each token, the profile sends one `POST` to the server's test-only probe endpoint, keeps the one token it needs, and drops the rest. Both examples serve this endpoint only in their compliance mode. Run these commands from the repository root:

```bash
pnpm --filter example-mcp-auth-http start -- --compliance            # http://127.0.0.1:8787/mcp
uv run python -m examples.a2a_gateway.serve --port 8788 --compliance  # http://127.0.0.1:8788
```

> **Warning:** compliance mode hands out valid tokens to anyone who can reach the server. Use it only for a run on a local machine or in CI. Only the command-line flag turns it on.

A server without a probe endpoint needs your own token source. Pass any object with one async method, `probe_token(kind) -> str`, as `token_provider` to `validate_profile` or to the profile's entry point. Each call must return a token that no earlier call returned. The interface is `ProbeTokenProvider` in `asap_compliance.validators`. `kind` is a `ProbeTokenKind`:

| Kind | Token | Profiles |
|------|-------|----------|
| `agent_jwt` | An Agent JWT with the grant | `mcp-auth-http` |
| `agent_jwt_out_of_grant` | An Agent JWT without the grant | `mcp-auth-http` |
| `external` | An access token with the grant | both |
| `external_out_of_grant` | An access token whose scopes give no grant | both |
| `external_wrong_audience` | An access token with the grant, for another audience | `a2a-bridge` |

A provider that has no token of a kind raises `ProbeTokenError`. Then only the checks that need that kind fail.

### Profile `mcp-auth-http`

Start [the example](https://github.com/asap-protocol/asap-protocol/blob/main/examples/mcp_auth_http/README.md) from the repository root:

```bash
pnpm install
pnpm --filter @asap-protocol/client run build && pnpm --filter @asap-protocol/mcp-auth run build
pnpm --filter example-mcp-auth-http start -- --compliance
```

`mcp-auth-http.json`:

```json
{
  "mcp_url": "http://127.0.0.1:8787/mcp",
  "probe_tokens_url": "http://127.0.0.1:8787/compliance/probe-tokens"
}
```

The fields of `McpAuthHttpComplianceConfig`. The defaults are those of the example. An unknown field is refused.

| Field | Default | Meaning |
|-------|---------|---------|
| `mcp_url` | required | The MCP endpoint of the server under test (`http` or `https`) |
| `probe_tokens_url` | none | The test-only probe endpoint. Without it, pass a `token_provider` |
| `timeout_seconds` | `10.0` | The deadline for each answer |
| `protected_tool` | `secure_action` | A tool that needs a token and a grant |
| `allowed_arguments` | `{"action": "deploy"}` | Arguments of a protected call that the grant allows |
| `constraint_violation_arguments` | `{"action": "forbidden-action"}` | Arguments that break the grant's constraint |
| `public_tool` | `echo` | A tool that the server serves with no token |
| `public_tool_arguments` | `{"message": "asap-compliance"}` | Arguments of the public tool call |
| `skip_checks` | `[]` | Check IDs to skip. A skipped check passes with the message `skipped` |
| `profile` | `mcp-auth-http` | The profile name. Any other value is refused |

The checks, in run order. A "challenge" is a `WWW-Authenticate` header with the `Bearer` scheme.

| Check | Request | Expected answer |
|-------|---------|-----------------|
| `public_tool` | No token, the public tool | HTTP 200 and a tool result |
| `auth_required` | No token, the protected tool | HTTP 401, a challenge with no `error`, `asap:auth_required` |
| `invalid_token` | A token that is not a JWT | HTTP 401, a challenge with `error="invalid_token"`, `asap:invalid_token` |
| `bad_signature` | An Agent JWT with a random signature | As `invalid_token` |
| `valid_jwt` | An Agent JWT with the grant | HTTP 200 and a tool result |
| `replayed_jwt` | One Agent JWT, sent twice | The first as `valid_jwt`, the second as `invalid_token` |
| `wrong_capability` | An Agent JWT without the grant | HTTP 200 and a tool error result `asap:capability_denied` |
| `constraint_violation` | An Agent JWT, arguments that break the constraint | HTTP 200 and a tool error result `asap:constraint_violation` |
| `external_valid_token` | One access token with the grant, sent twice | Both as `valid_jwt`: there is no replay check on access tokens |
| `external_wrong_capability` | An access token whose scopes give no grant | HTTP 403, a challenge with `error="insufficient_scope"`, `asap:capability_denied` |
| `external_constraint_violation` | An access token, arguments that break the constraint | As `constraint_violation` |
| `undeclared_tool` | An Agent JWT, a tool that the server does not declare | As `external_wrong_capability` (deny by default) |
| `www_authenticate_on_401` | Every answer of the run | Each HTTP 401 has a challenge. Fails if the run saw no 401 |
| `token_not_echoed` | Every answer of the run | No body or header holds a token that the run sent, or a part of one that is 16 characters or longer |

### Profile `a2a-bridge`

Start [the gateway](https://github.com/asap-protocol/asap-protocol/blob/main/examples/a2a_gateway/README.md) from the repository root:

```bash
uv run python -m examples.a2a_gateway.serve --port 8788 --compliance
```

The profile trusts its config only: the agent URN that the card must name (`expected_agent_urn`), and the public keys that may sign the card (`card_keys`). Take both from your own configuration: the URN of the agent that you expect, and the key that the agent's owner gives you out of band. The profile never reads a key or a URN from the target, because a check against values that the target chose would prove nothing. Each key is trusted for `expected_agent_urn` only.

The demo gateway makes a new card key at each start, so a demo run copies the key from the probe set. This step exists only for the demo. It writes `a2a-bridge.json`:

```bash
curl -s -X POST http://127.0.0.1:8788/compliance/probe-tokens | python3 -c '
import json, sys
key = json.load(sys.stdin)["card_key"]
json.dump({
    "base_url": "http://127.0.0.1:8788",
    "allow_private_targets": True,
    "expected_agent_urn": "urn:asap:agent:a2a-gateway-demo",
    "card_keys": {key["kid"]: key["jwk"]},
    "probe_tokens_url": "http://127.0.0.1:8788/compliance/probe-tokens",
}, open("a2a-bridge.json", "w"), indent=2)'
python run_profile.py a2a-bridge a2a-bridge.json
```

The fields of `A2ABridgeComplianceConfig`. The defaults are those of the example. An unknown field is refused, and an error message never repeats the input.

| Field | Default | Meaning |
|-------|---------|---------|
| `base_url` | required | The origin of the gateway: scheme, host, and port, with no path |
| `expected_agent_urn` | required | The agent URN that the card must name |
| `card_keys` | required | `kid` → public JWK of each key that may sign the card. At least one. A private key is refused |
| `probe_tokens_url` | none | The test-only probe endpoint. Without it, pass a `token_provider` |
| `allow_private_targets` | `false` | Allow a target on a loopback or private address. See below |
| `timeout_seconds` | `10.0` | The deadline for each answer |
| `skill_id` | `greet` | The skill that the calls select |
| `allowed_arguments` | `{"name": "Ada", "language": "en"}` | Arguments that the grant allows |
| `constraint_violation_arguments` | `{"name": "Ada", "language": "fr"}` | Arguments that break the grant's constraint |
| `skip_checks` | `[]` | Check IDs to skip. A skipped check passes with the message `skipped` |
| `profile` | `a2a-bridge` | The profile name. Any other value is refused |

**Private targets.** `allow_private_targets` is off by default. Then `base_url` and `probe_tokens_url` must be `https` URLs to a public host: no IP address that is not public, no `localhost`, and no user info. This is the URL rule of card ingest. When the switch is on, an `https` URL may name any host, and an `http` URL is accepted only for a loopback or private IP address or a `localhost` name, so that a token never crosses a public network in clear text. Turn it on only for a local run, or for a CI run against a server on the same machine or private network. The `mcp-auth-http` profile has no such switch: it accepts any `http` or `https` URL.

**What a verified card proves.** `card_signature` proves who signed the card. It does not prove where the card was served from, or that it is fresh: a signed card can be served again from another URL for as long as its key is trusted. No check of this profile claims either one. The calls go to `<base_url>/a2a`, never to a URL read from the card, so probe tokens go only to the configured origin.

The checks, in run order. The card checks send no token, and fetch each document once per run.

| Check | Request | Expected answer |
|-------|---------|-----------------|
| `card_well_known` | `GET /.well-known/agent-card.json` | HTTP 200 and a JSON object |
| `card_a2a_v1` | That card | It parses as an A2A v1.0 Agent Card. A v0.3 card fails |
| `card_signature` | That card | A signature by a configured key verifies, no signature fails, and the key may sign for the agent that the card names |
| `card_agent_urn` | That card | Its ASAP extension names `expected_agent_urn` |
| `card_jsonrpc_interface` | That card | It lists a `JSONRPC` interface with protocol version `1.0` at `<base_url>/a2a` |
| `card_manifest_same_agent` | `GET /.well-known/asap/manifest.json` | On HTTP 200: the same URN, `name`, and `version` as the card. A 404 passes; any other answer fails |
| `external_valid_token` | One access token with the grant, sent twice | Both times: HTTP 200 and a JSON-RPC result whose message has a `data` part |
| `auth_required` | No token | HTTP 401, a challenge with no `error`, `asap:auth_required` |
| `invalid_token` | A token that is not a JWT | HTTP 401, a challenge with `error="invalid_token"`, `asap:invalid_token` |
| `bad_signature` | The header and claims of a fresh access token, with a random signature | As `invalid_token` |
| `external_wrong_audience` | An access token with the grant, for another audience | As `invalid_token` |
| `external_wrong_capability` | An access token whose scopes give no grant for the skill | HTTP 403, a challenge with `error="insufficient_scope"`, `asap:capability_denied` |
| `external_constraint_violation` | An access token, arguments that break the constraint | HTTP 403 with no `WWW-Authenticate` header, `access_denied`, `asap:constraint_violation` |
| `undeclared_skill` | An access token, a skill that the card does not list | HTTP 200 and the JSON-RPC error `-32602` |
| `www_authenticate_on_401` | Every answer of the run, the card and the manifest too | Each HTTP 401 has a challenge. Fails if the run saw no 401 |
| `token_not_echoed` | Every answer of the run | As in `mcp-auth-http` |

### What the profiles do not check

A remote checker cannot cause every case safely, or it cannot sign the token that the case needs. Other tests in the repository cover these cases:

- **An expired token, or a token for another audience** (`mcp-auth-http`): the harness cannot sign one, and the probe endpoint gives none. The tests of `@asap-protocol/mcp-auth` cover them.
- **An expired access token, and an ASAP Agent JWT sent as an A2A credential** (`a2a-bridge`): the harness cannot sign either one. The gateway answers both as `invalid_token`. The tests of the gateway and of the external-token verifier cover them.
- **HTTP 503**: a key source that cannot be reached, or a full replay cache. The tests of `@asap-protocol/mcp-auth` and of the gateway cover them.
- **JSON-RPC `-32004`** for a method other than `SendMessage` (`a2a-bridge`): the tests of the gateway cover it.
- **The origin and the freshness of a card**: no check proves them (see above).

### Live tests in this repository

The tests of `asap-compliance` hold one live test per HTTP profile. It runs the profile twice in a row against the example, so the second run proves that each token is fresh. A live test is skipped unless an environment variable names a running example in compliance mode:

```bash
ASAP_COMPLIANCE_MCP_HTTP_URL=http://127.0.0.1:8787/mcp \
ASAP_COMPLIANCE_A2A_GATEWAY_URL=http://127.0.0.1:8788 \
uv run pytest asap-compliance/tests/ -m "mcp_http_live or a2a_gateway_live"
```

The `a2a-bridge` live test copies the card key from the probe set, as the demo step above does. In CI, the job `compliance (live flows)` starts both examples and runs these tests with `scripts/compliance_live_flows.py`; it fails when either profile test did not pass, a skip included.

## See Also

- [asap-compliance README](../../asap-compliance/README.md) — Package overview
- [Identity Signing](identity-signing.md) — How to sign manifests
- [Testing](../testing.md) — General testing guide for ASAP development
- [Interoperability](interoperability.md) — The A2A and HTTP MCP flows that the `a2a-bridge` and `mcp-auth-http` profiles check
