# ASAP Compliance Harness

Protocol compliance testing suite for ASAP (Async Simple Agent Protocol) implementations.

## Overview

The Compliance Harness validates that ASAP agents follow the protocol specification. Third parties can use this package to certify their agents are ASAP-compliant, enabling ecosystem interoperability.

## Installation

```bash
uv add asap-compliance
# or
pip install asap-compliance
```

## Usage

Run compliance tests against an agent:

```bash
pytest --asap-agent-url https://your-agent.example.com -m asap_compliance
```

Or set the agent URL via environment variable:

```bash
ASAP_AGENT_URL=https://your-agent.example.com pytest -m asap_compliance
```

## Test Categories

- **handshake** (implemented): Agent connection, health/content-type, manifest schema,
  signature verification, version negotiation
- **schema** (implemented): Pydantic model compliance (Envelope, TaskRequest,
  TaskResponse, McpToolResult, MessageAck, extensions, extra='forbid')
- **state**: Task state machine transitions

## Profiles

A profile checks one integration against a running server. It returns one result per check ID, and a failed check says what it expected and what it got. The [compliance testing guide](https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/compliance-testing.md) lists every config field and every check ID.

- **mcp-auth-bridge** (v1.3.0+, requires `asap-protocol>=2.5.0`): Stdio MCP Auth Bridge release gate — JWT paths, grants, constraints, manifest ⊆ registered tools. See [MCP Auth Bridge](../docs/adapters/mcp-auth-bridge.md) and `examples/mcp_auth_bridge/README.md`.
- **mcp-auth-http**: an HTTP MCP server protected by `@asap-protocol/mcp-auth`, checked over plain HTTP, with no MCP SDK. Each refusal must carry the right `asap:*` code in the right layer (HTTP 401 or 403 with a `Bearer` challenge, or a tool error result). It also checks Agent JWT replay, access-token reuse, a tool that the server does not declare, and that no answer echoes a token. Config: `McpAuthHttpComplianceConfig`; entry point: `validate_mcp_auth_http`.
- **a2a-bridge**: an A2A v1.0 agent served by an ASAP gateway. The Agent Card must be at the well-known path, parse as A2A v1.0, verify under the keys in your config, name the agent URN in your config, list the gateway's JSON-RPC interface, and match the ASAP manifest on the same origin. Then one `SendMessage` per case: a valid access token (sent twice), no token, a malformed, forged, or wrong-audience token, a token without the grant, arguments that break the grant's constraint, and a skill that the card does not list. Config: `A2ABridgeComplianceConfig`; entry point: `validate_a2a_bridge`.

The two HTTP profiles need a fresh token for each request. By default they get them from the server's test-only probe endpoint (`probe_tokens_url`); you can pass your own `ProbeTokenProvider` instead. This version of the package needs `asap-protocol` 2.6.0 or later.

Select a profile by name and run it:

```python
from asap_compliance import profile_config, validate_profile

config = profile_config("mcp-auth-http", {
    "mcp_url": "http://127.0.0.1:8787/mcp",
    "probe_tokens_url": "http://127.0.0.1:8787/compliance/probe-tokens",
})
result = validate_profile(config)
for check in result.checks:
    print("PASS" if check.passed else "FAIL", check.name, check.message)
```

Or with pytest: put the config fields in a JSON file, and write one test that takes the `compliance_profile` fixture:

```python
# test_profile.py
from asap_compliance import validate_profile


def test_profile(compliance_profile):
    result = validate_profile(compliance_profile)
    assert result.passed, [check for check in result.checks if not check.passed]
```

```bash
pytest test_profile.py --asap-profile a2a-bridge --asap-profile-config a2a-bridge.json
```

### Protocol v2.2 and Compliance Harness v2

This package focuses on **handshake**, **schema**, **state**, and **SLA** validators for third-party certification. **Compliance Harness v2** (identity, streaming, errors, versioning, batch, audit checks against the v2.2 spec) ships with the main [`asap-protocol`](https://pypi.org/project/asap-protocol/) library — use `from asap.testing.compliance import run_compliance_harness_v2` on an ASGI app. See the [changelog](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md) for v2.2.0 details.

## Programmatic Usage

```python
# Handshake validation (against live agent)
from asap_compliance import ComplianceConfig, validate_handshake

config = ComplianceConfig(agent_url="https://your-agent.example.com")
result = validate_handshake(config)
if result.passed:
    print("Agent is compliant")
else:
    for check in result.checks:
        if not check.passed:
            print(f"FAIL: {check.name} - {check.message}")

# Schema validation (static, for envelope/payload dicts)
from asap_compliance import validate_schema

envelope_dict = {"asap_version": "0.1", "sender": "urn:asap:agent:a", ...}
schema_result = validate_schema(envelope_dict)
if schema_result.passed:
    print("Envelope and payload schema valid")
```

## Development

```bash
cd asap-compliance
uv sync
uv run pytest
```
