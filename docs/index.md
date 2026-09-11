# ASAP Protocol

**ASAP (Async Simple Agent Protocol)** is a protocol for agent-to-agent communication with typed messages, async transports, discovery, and authorization.

**Latest on PyPI:** **v2.5.5** ([CHANGELOG](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md#255---2026-09-11), [GitHub Release](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5)). **v2.5.5** is a **security & quality patch** (OpenAPI path-param climb, registry URN overwrite guard, marketplace SSRF DNS pin, capability-replace consent, fail-closed reactivate, Agent JWT persist/revoke races); see [Migration (v2.5.4 → v2.5.5)](migration.md#upgrading-from-v254-to-v255). **v2.5.4** (Distribution Loop) — thin starters, [Build for agents](guides/build-for-agents.md), homepage agent-first CTA routing, telemetry ops; see [Migration (v2.5.3 → v2.5.4)](migration.md#upgrading-from-v253-to-v254). **v2.5.3** (Adapter Lab II) — workflow connectors, automation security, experimental MAF / NAT guides, plus small DX fixes; see [Migration (v2.5.2 → v2.5.3)](migration.md#upgrading-from-v252-to-v253). **v2.5.2** is the security & correctness follow-up — see [Migration (v2.5.1 → v2.5.2)](migration.md#upgrading-from-v251). **v2.5.0 MCP Auth Bridge** — opt-in Agent JWT + capability grants for native stdio MCP (`protect_server`); see [MCP Auth Bridge](adapters/mcp-auth-bridge.md). **v2.5.1** is a behavior-preserving code quality patch; see [Migration (v2.5.0 → v2.5.1)](migration.md#upgrading-from-v250-to-v251). **`asap-compliance` 1.3.0** on PyPI (tag [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1)). **TypeScript npm line: v2.4.1** — [`@asap-protocol/client`](https://www.npmjs.com/package/@asap-protocol/client), [`@asap-protocol/mastra`](integrations/mastra.md), [`@asap-protocol/openai-agents`](integrations/openai-agents.md) (`@asap-protocol/mcp-auth` not in the v2.5.x npm line). Next product train: **Formal Spec** ([prd-v2.5.5-formal-spec-interop.md](../product/prd/prd-v2.5.5-formal-spec-interop.md)). Upgrade: [v2.5.4 → v2.5.5](migration.md#upgrading-from-v254-to-v255) · [v2.5.3 → v2.5.4](migration.md#upgrading-from-v253-to-v254) · [v2.5.2 → v2.5.3](migration.md#upgrading-from-v252-to-v253) · [v2.5.1 → v2.5.2](migration.md#upgrading-from-v251) · [v2.5.0 → v2.5.1](migration.md#upgrading-from-v250-to-v251) · [v2.4.1 → v2.5.0](migration.md#upgrading-from-v241-to-v250) · [v2.4.0 → v2.4.1](migration.md#upgrading-from-v240-to-v241).

## Features

- **Typed Messages**: Full Pydantic type safety for all protocol messages
- **Async-First**: Built on `asyncio` for high-performance agent communication
- **State Management**: Built-in task state machine with persistence support (`AsyncSnapshotStore` / `AsyncMeteringStore` in v2.2+)
- **Transport Agnostic**: Clean separation between protocol logic and transport capability (HTTP JSON-RPC, WebSocket, SSE)
- **Observability**: First-class tracking with correlation IDs and trace IDs
- **Security & authorization (v2.2+, WebAuthn real in v2.2.1)**: Per-runtime Host/Agent JWTs, capability grants with constraints, approval flows, opt-in WebAuthn (`asap-protocol[webauthn]`) for browser-controlled and high-risk capability registration — see [Security](security.md) and [Migration](migration.md)
- **MCP Auth Bridge (v2.5.0+)**: Opt-in `protect_server` wraps native stdio `MCPServer` with Agent JWT verification and grant enforcement (Mode A); MCP-over-ASAP envelopes remain Mode B — [MCP Auth Bridge](adapters/mcp-auth-bridge.md), [MCP integration](mcp-integration.md)
- **Adapter Lab II (v2.5.3 docs train)**: [Workflow connectors](integrations/workflow-connectors.md) (OpenAPI → ASAP skills), [Automation connector security](guides/automation-connector-security.md), experimental [Microsoft Agent Framework](integrations/microsoft-agent-framework.md) and [NeMo Agent Toolkit](integrations/nemo-agent-toolkit.md) interop guides
- **Distribution Loop (v2.5.4)**: [Build for agents](guides/build-for-agents.md) + thin starters under `examples/starters/` (OpenAPI provider, TypeScript consumer, MCP Auth Bridge)
- **Edge-AI discovery (v2.4.0+)**: Optional `capabilities.hardware` / `inference` on manifests; [Transport](transport.md#hardware-and-inference-capabilities-v24), [ShellClaw registry guide](guides/shellclaw-registry.md), [registry examples](examples/registry-shellclaw.md)
- **Adoption tools (v2.3.0+)**: [OpenAPI adapter](adapters/openapi.md), [TypeScript client](sdks/typescript.md) (`@asap-protocol/client@2.4.1`), [Mastra adapter](integrations/mastra.md) (`@asap-protocol/mastra@2.4.1`), [OpenAI Agents SDK adapter](integrations/openai-agents.md) (`@asap-protocol/openai-agents@2.4.1`), [Auto-registration](registry/auto-registration.md), [Capability escalation](capabilities/escalation.md), [ASAP HTTP challenge](transport/asap-challenge.md)

## Installation

```bash
# Using uv (recommended) — installs latest on PyPI (currently 2.5.5)
uv add asap-protocol

# Using pip (pin for reproducible upgrades)
pip install asap-protocol==2.5.5
```

📦 **Available on [PyPI](https://pypi.org/project/asap-protocol/):** `asap-protocol` **2.5.5** ([GitHub Release `v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5)). **`asap-compliance` 1.3.0** via tag [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1). TypeScript `@asap-protocol/*` packages remain at **2.4.1**.

## Quick Start

```python
import asyncio
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.client import ASAPClient

async def main():
    request = TaskRequest(
        conversation_id="conv_01HX5K3MQVN8",
        skill_id="echo",
        input={"message": "hello from client"},
    )
    envelope = Envelope(
        asap_version="0.1",  # envelope schema version (see asap.models.constants.ASAP_PROTOCOL_VERSION)
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:echo-agent",
        payload_type="task.request",
        payload=request.model_dump(),
    )
    async with ASAPClient("http://127.0.0.1:8001") as client:
        response = await client.send(envelope)
        print(response.payload)

if __name__ == "__main__":
    asyncio.run(main())
```

`ASAPClient` sends the **`ASAP-Version`** header for wire-level negotiation (`2.1` / `2.2`); that is separate from the envelope `asap_version` string above. See [Migration (v2.1.x → v2.2.0)](migration.md) and [ADR-019](adr/ADR-019-unified-versioning.md).

## CLI

ASAP provides a CLI for schema management and (v1.2) key/manifest operations:

```bash
# Show version
asap --version

# Export all JSON schemas
asap export-schemas --output-dir ./schemas

# List available schemas
asap list-schemas

# Show a specific schema
asap show-schema agent

# v1.2: Ed25519 keys and signed manifests
asap keys generate -o key.pem
asap manifest sign -k key.pem manifest.json
asap manifest verify signed.json
asap manifest info signed.json

# Compliance Harness v2 against a deployed agent (HTTP(S))
asap compliance-check --url https://your-agent.example.com --exit-on-fail

# Tamper-evident audit export (SQLite store)
asap audit export --store sqlite --db ./asap_state.db --format json --verify-chain
```

See [CLI reference](cli.md) (all commands, exit codes, `compliance-check`, `audit export`), [Audit log](audit.md), [CI: compliance gate](ci-compliance.md), and [Identity Signing](guides/identity-signing.md) for key and manifest workflows.

## Documentation

**See also:** [Build for agents](guides/build-for-agents.md) — agent-first onboarding into the three thin starters (OpenAPI provider, TypeScript consumer, MCP Auth Bridge).

- [OpenAPI adapter](adapters/openapi.md) — derive ASAP skills and an upstream proxy from OpenAPI 3.x (`asap.adapters.openapi`)
- [MCP Auth Bridge](adapters/mcp-auth-bridge.md) — opt-in Agent JWT + capability enforcement for native stdio `MCPServer` (`asap.mcp.auth.protect_server`)
- [Workflow connectors](integrations/workflow-connectors.md) — n8n / Activepieces-style workflow HTTP APIs → ASAP skills via OpenAPI (Adapter Lab II)
- [Automation connector security](guides/automation-connector-security.md) — secrets, TLS, webhooks, grants, and MCP Path A notes for connectors
- [Microsoft Agent Framework](integrations/microsoft-agent-framework.md) — research / experimental MAF ↔ ASAP interop (no .NET SDK)
- [NeMo Agent Toolkit](integrations/nemo-agent-toolkit.md) — experimental NAT ↔ ASAP Path A demo (`protect_server` + MCP)
- [TypeScript client SDK](sdks/typescript.md) — `@asap-protocol/client@2.4.1` on npm (browser + Node; optional LLM adapters; edge-AI registry fields)
- [Mastra adapter](integrations/mastra.md) — `@asap-protocol/mastra@2.4.1`: ASAP capabilities as `@mastra/core` tools + streaming bridge
- [OpenAI Agents SDK adapter](integrations/openai-agents.md) — `@asap-protocol/openai-agents@2.4.1`: ASAP capabilities as `@openai/agents` tools + handoff-oriented remote agent helper (`@openai/agents`, distinct from `@asap-protocol/client/adapters/openai`)
- [CLI reference](cli.md) — all `asap` commands, including `compliance-check`, `audit export`, and exit codes
- [Audit log](audit.md) — hash chain model, export formats, tamper checks
- [API Reference](api-reference.md)
- [Observability](observability.md)
- [Error Handling](error-handling.md)
- [Testing](testing.md)
- [Raw Fetch (non-Python)](raw-fetch.md) — Fetch registry.json and revoked_agents.json directly (curl, fetch); implement your own client in any language
- [v1.1 Security Model](security/v1.1-security-model.md) — OAuth2 trust limitations, Custom Claims, allowlist (see also [decision record § security](https://github.com/asap-protocol/asap-protocol/blob/main/product/decision-records/03-security.md))
- [Registry verification review (admin)](guides/registry-verification-review.md) — How to vet and approve Verified badge requests for the Lite Registry
- [ShellClaw static manifest registration](guides/shellclaw-registry.md) — `online_check: false`, GitHub Pages manifest URLs, IssueOps vs auto-registration
- [Lite Registry auto-registration](registry/auto-registration.md) — Bot PR flow, OAuth2, payloads, rejections, and upgrading to Verified

### v1.1 features (API reference & guides)

| Feature | Description | Where |
|:--------|:-------------|:------|
| **OAuth2 / Custom Claims** | Server and client auth; identity binding via JWT claims | [Transport](transport.md), [Security](security.md), examples: `auth_patterns` |
| **WebSocket** | Real-time transport; MessageAck for reliability (see [Q16 — WebSocket Message Ack](https://github.com/asap-protocol/asap-protocol/blob/main/product/decision-records/02-protocol.md#question-16-websocket-message-acknowledgment)) | [Transport](transport.md), `asap.transport.ws` |
| **Webhooks** | Signed POST callbacks to URLs; SSRF checks, retry, DLQ | [API Reference](api-reference.md) (`asap.transport`), `WebhookDelivery`, `WebhookRetryManager` |
| **Discovery** | Well-known manifest, Lite Registry, health endpoint | [Transport](transport.md), `asap.discovery` |
| **State Storage** | SQLite backend, env-based backend selection | [State Management](state-management.md), [Best Practices: Failover](best-practices/agent-failover-migration.md), examples: `storage_backends`, `state_migration` |
| **Health** | `GET /.well-known/asap/health` for liveness | [Transport](transport.md), `asap.discovery.health` |

## Contact

**General inquiries** about the protocol or project: [info@asap-protocol.com](mailto:info@asap-protocol.com).

For vulnerability reports, use **[SECURITY.md](https://github.com/asap-protocol/asap-protocol/blob/main/SECURITY.md)** (GitHub Private Vulnerability Reporting), not email. Technical questions work well via [GitHub Discussions](https://github.com/asap-protocol/asap-protocol/discussions) or [Issues](https://github.com/asap-protocol/asap-protocol/issues).
