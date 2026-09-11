# ASAP: Async Simple Agent Protocol

*✨ From **agents**, for **agents**. Delivering reliability, **as soon as possible.***

![ASAP Protocol Banner](https://raw.githubusercontent.com/asap-protocol/asap-protocol/main/.github/assets/asap-protocol-banner.png)


> A production-ready protocol for agent-to-agent communication and task coordination.

**Quick Info**: [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5) · PyPI [`asap-protocol`](https://pypi.org/project/asap-protocol/) | `Apache 2.0` | `Python 3.13+` | [Documentation](docs/index.md) | [Changelog](CHANGELOG.md)

> 📦 **Install** — [`asap-protocol` **2.5.5** on PyPI](https://pypi.org/project/asap-protocol/) (Python) · [`@asap-protocol/client` on npm](https://www.npmjs.com/package/@asap-protocol/client) (TypeScript)

🚀 **Live now** our [**agentic marketplace**](https://asap-protocol.com/) — browse agents, register yours, request verification.

## Why ASAP?

Multi-agent systems hit three walls that point-to-point agent protocols often leave open:

1. **Connection sprawl** — pairwise HTTP does not scale as orchestrators fan out.
2. **State drift** — long workflows stall without durable task state and resumability.
3. **Fragmentation** — delegation, artifacts, and MCP tool calls end up in incompatible layers.

**ASAP** answers with a schema-first protocol and reference SDKs in **Python** and **TypeScript**:

- **Resumable orchestration** — task state machine, snapshot store, SSE streaming, and built-in `trace_id` / `correlation_id`.
- **One typed envelope** — tasks, MCP tool execution, and artifact exchange on the same JSON Schema contract.
- **Production trust** — Ed25519 signed manifests, Host/Agent JWTs, constrained capabilities, OAuth2, opt-in WebAuthn — plus the **MCP Auth Bridge** (v2.5.0) for scoped native `tools/call`.
- **Ecosystem-ready** — [agentic marketplace](https://asap-protocol.com/), Lite Registry, edge-AI discovery, OpenAPI import, and framework adapters ([Python & npm](#framework-ecosystem)).

Plain HTTP between two agents is enough for the simplest cases. ASAP is built for **multi-agent orchestration**, **stateful workflows**, and **governed capability access** in production — see [documentation](docs/index.md) and the feature table below.

### Key Features

| Area | Highlights | Docs |
| --- | --- | --- |
| Stateful orchestration | Task state machine, snapshotting, resumable workflows | [State management](docs/state-management.md) |
| Schema-first | Pydantic v2 + JSON Schema for cross-agent interchange | [API reference](docs/api-reference.md) |
| Async-native | `asyncio` + `httpx`; sync and async handlers | [Transport](docs/transport.md) |
| MCP integration | Tool execution and coordination in one envelope (Mode B) | [MCP integration](docs/mcp-integration.md) |
| MCP Auth Bridge | Opt-in Agent JWT + capability grants on native stdio MCP `tools/call` (Mode A) | [MCP Auth Bridge](docs/adapters/mcp-auth-bridge.md) |
| Observability | `trace_id` and `correlation_id` for debugging | [Observability](docs/observability.md) |
| Security | OAuth2/JWT, Ed25519 manifests, mTLS, rate limiting | [Security](docs/security.md) |
| Identity & capabilities | Host/Agent JWTs, constrained grants, approval flows, opt-in WebAuthn | [Capabilities](docs/capabilities/index.md) |
| Streaming & wire protocol | SSE `/asap/stream`, JSON-RPC batch, `ASAP-Version` negotiation | [Transport](docs/transport.md) |
| Adoption tools | OpenAPI adapter, `@asap-protocol/client`, auto-registration, escalation | [Migration (v2.2 → v2.3)](docs/migration.md#upgrading-from-v22x-to-v230) |
| Edge-AI discovery | Hardware/inference manifests, registry mirror, marketplace filters | [ShellClaw guide](docs/guides/shellclaw-registry.md) |
| Framework adapters (npm) | `@asap-protocol/mastra` and `@asap-protocol/openai-agents` tool bridges | [Mastra](docs/integrations/mastra.md) · [OpenAI Agents](docs/integrations/openai-agents.md) |
| Economics | Usage metering, delegation tokens, SLA breach alerts | [Audit log](docs/audit.md) |

Full overview and upgrade paths: [docs/index.md](docs/index.md).

### Framework Ecosystem

ASAP meets agents where they run — optional Python extras, npm tool bridges, and protocol-native MCP.

| Runtime | Integrations | Docs |
| --- | --- | --- |
| **Python** | LangChain, CrewAI, LlamaIndex, PydanticAI, SmolAgents, OpenClaw (`pip install "asap-protocol[extra]"`); Vercel AI SDK router; MCP; MCP Auth Bridge; A2H; OpenAPI workflow connectors (Lab II) | [OpenClaw](docs/guides/openclaw-integration.md) · [Vercel AI SDK](docs/guides/vercel-ai-sdk.md) · [MCP](docs/mcp-integration.md) · [MCP Auth Bridge](docs/adapters/mcp-auth-bridge.md) · [Workflow connectors](docs/integrations/workflow-connectors.md) · [NeMo Agent Toolkit](docs/integrations/nemo-agent-toolkit.md) (experimental) |
| **TypeScript** (npm) | `@asap-protocol/client` (Vercel AI / OpenAI / Anthropic adapters), `@asap-protocol/mastra`, `@asap-protocol/openai-agents` | [TypeScript SDK](docs/sdks/typescript.md) · [Mastra](docs/integrations/mastra.md) · [OpenAI Agents](docs/integrations/openai-agents.md) · [Microsoft Agent Framework](docs/integrations/microsoft-agent-framework.md) (research) |

## Installation

We recommend using [uv](https://github.com/astral-sh/uv) for dependency management:

```bash
uv add asap-protocol  # latest on PyPI (currently 2.5.5)
```

Or with pip:

```bash
pip install asap-protocol==2.5.5
```

**TypeScript** (npm, **`2.4.1`** — unchanged for v2.5.5; `@asap-protocol/mcp-auth` HTTP middleware still deferred):

- [`@asap-protocol/client`](https://www.npmjs.com/package/@asap-protocol/client) — [SDK docs](docs/sdks/typescript.md)
- [`@asap-protocol/mastra`](https://www.npmjs.com/package/@asap-protocol/mastra) — [docs](docs/integrations/mastra.md) · [demo](apps/example-mastra/README.md)
- [`@asap-protocol/openai-agents`](https://www.npmjs.com/package/@asap-protocol/openai-agents) — [docs](docs/integrations/openai-agents.md) · [demo](apps/example-openai-agents/README.md)

```bash
npm install @asap-protocol/client@2.4.1
npm install @asap-protocol/mastra@2.4.1 @asap-protocol/client @mastra/core zod
npm install @asap-protocol/openai-agents@2.4.1 @asap-protocol/client @openai/agents zod
```

**Python v2.5.5** (security & quality patch) is **shipped** — see [Migration (v2.5.4 → v2.5.5)](docs/migration.md#upgrading-from-v254-to-v255). Dist Loop starters remain: [Build for agents](docs/guides/build-for-agents.md) · [starters](examples/starters/README.md).

## Quick Start

**Run the demo** (echo agent + coordinator in one command):

```bash
uv run python -m asap.examples.run_demo
```

**Build your first agent** [here](docs/tutorials/first-agent.md) — server setup, client code, step-by-step (~15 min).

[19 examples](src/asap/examples/README.md): orchestration, state migration, MCP, OAuth2, WebSocket, resilience.

## Testing

```bash
uv run pytest -n auto --tb=short
```

With coverage (separate run — do not combine with `-n auto`):

```bash
uv run pytest --tb=short --cov=asap --cov-report=term-missing --cov-fail-under=85
```

[Testing Guide](https://github.com/asap-protocol/asap-protocol/blob/main/docs/testing.md) (structure, fixtures, property/load/chaos tests). [Contributing](https://github.com/asap-protocol/asap-protocol/blob/main/CONTRIBUTING.md) (dev setup, CI).

### Compliance Harness

Validate that your agent follows the ASAP protocol:

```bash
uv add "asap-compliance>=1.3.0"
pytest --asap-agent-url https://your-agent.example.com -m asap_compliance
```

For **MCP Auth Bridge** stdio gates (`mcp-auth-bridge` profile), use **`asap-compliance` 1.3.0+** with **`asap-protocol` 2.5.0+** — published on PyPI via tag [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1).

See [Compliance Testing Guide](https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/compliance-testing.md) for handshake, schema and state machine validation.

## Documentation

**Learn**
- [Docs](docs/index.md) | [API Reference](docs/api-reference.md)
- [MCP Auth Bridge](docs/adapters/mcp-auth-bridge.md) — v2.5.0 opt-in JWT + capability grants for native stdio MCP
- [TypeScript client SDK](docs/sdks/typescript.md) — `@asap-protocol/client` (identity, capabilities, streaming, adapters)
- [Tutorials](https://github.com/asap-protocol/asap-protocol/tree/main/docs/tutorials) — First agent to production checklist
- [Migration from A2A/MCP](https://github.com/asap-protocol/asap-protocol/blob/main/docs/migration.md)
- [Raw Fetch (non-Python)](https://github.com/asap-protocol/asap-protocol/blob/main/docs/raw-fetch.md) — Fetch registry.json and revoked_agents.json with curl/fetch; implement your own client.

**Deep Dive**
- [State Management](https://github.com/asap-protocol/asap-protocol/blob/main/docs/state-management.md) | [Best Practices: Failover & Migration](https://github.com/asap-protocol/asap-protocol/blob/main/docs/best-practices/agent-failover-migration.md) | [Error Handling](https://github.com/asap-protocol/asap-protocol/blob/main/docs/error-handling.md)
- [Transport](https://github.com/asap-protocol/asap-protocol/blob/main/docs/transport.md) | [Security](https://github.com/asap-protocol/asap-protocol/blob/main/docs/security.md) | [Security Model](https://github.com/asap-protocol/asap-protocol/blob/main/docs/security/v1.1-security-model.md) (OAuth2 trust, Custom Claims)
- [Identity Signing](https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/identity-signing.md) | [Compliance Testing](https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/compliance-testing.md) | [Migration v1.1 to v1.2](https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/migration-v1.1-to-v1.2.md) | [mTLS](https://github.com/asap-protocol/asap-protocol/blob/main/docs/security/mtls.md)
- [Observability](https://github.com/asap-protocol/asap-protocol/blob/main/docs/observability.md) | [Testing](https://github.com/asap-protocol/asap-protocol/blob/main/docs/testing.md)

**Decisions & Operations**
- [ADRs](https://github.com/asap-protocol/asap-protocol/tree/main/docs/adr) — 19 Architecture Decision Records
- [Tech Stack](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/architecture/tech-stack-decisions.md) — Rationale for Python, Pydantic, Next.js choices
- [Deployment](https://github.com/asap-protocol/asap-protocol/blob/main/docs/deployment/kubernetes.md) | [Troubleshooting](https://github.com/asap-protocol/asap-protocol/blob/main/docs/troubleshooting.md)

**Release**
- [Changelog](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md) | **[PyPI listing](https://pypi.org/project/asap-protocol/)** — `https://pypi.org/project/asap-protocol/` (install: `pip install asap-protocol`)

## CLI

```bash
asap --version                                    # Show version
asap list-schemas                                 # List JSON schemas
asap export-schemas                               # Export schemas to disk
asap validate-schema payload.json                 # Validate JSON against a schema
asap compliance-check --url https://agent.example # Remote Compliance Harness v2
asap audit export --store memory --format json    # Export audit log (stdout)
asap keys generate -o key.pem                     # Ed25519 keypair
asap manifest sign -k key.pem manifest.json       # Sign agent manifest
asap delegation create -d <urn> -s read -k key.pem --delegator <urn>
asap trace <trace-id> --log-file asap.log         # Visualize request flow from logs
```

See [docs/cli.md](docs/cli.md) for delegation tokens, schema validation, trace visualization, REPL, and full flag reference. Run `asap --help` for your installed version.

## Version History

High-level only — see **[Changelog](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md)** and the **[docs index](https://github.com/asap-protocol/asap-protocol/blob/main/docs/index.md#v11-features-api-reference--guides)** for full notes.

| Version | What shipped |
| :-- | :-- |
| **v2.5.5** | **Security & quality patch** — **[GitHub Release `v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5)** · OpenAPI path-param climb, registry URN overwrite guard, marketplace SSRF DNS pin, capability-replace consent, fail-closed reactivate, Agent JWT persist/revoke races. npm `@asap-protocol/*` remain **2.4.1**. See [CHANGELOG](CHANGELOG.md#255---2026-09-11) and [Migration (v2.5.4 → v2.5.5)](docs/migration.md#upgrading-from-v254-to-v255) |
| **v2.5.4** | **Distribution Loop** — **[GitHub Release `v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4)** · thin starters (`examples/starters/`), [Build for agents](docs/guides/build-for-agents.md), homepage agent-first CTAs, telemetry ops (no public metrics UI). See [CHANGELOG](CHANGELOG.md#254---2026-07-18), [PRD](product/prd/prd-v2.5.4-distribution-loop.md), and [Migration (v2.5.3 → v2.5.4)](docs/migration.md#upgrading-from-v253-to-v254) |
| **v2.5.3** | **Adapter Lab II** — **[GitHub Release `v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3)** · workflow OpenAPI connectors + security guide, experimental MAF / NeMo guides, JSON-safe `-32602`, MCP example client DX. See [CHANGELOG](CHANGELOG.md#253---2026-07-14), [PRD](product/prd/prd-v2.5.3-adapter-lab-ii.md), and [Migration (v2.5.2 → v2.5.3)](docs/migration.md#upgrading-from-v252-to-v253) |
| **v2.5.2** | **Security & correctness follow-up** — opt-in operator API auth, `extra="forbid"` ingress, Redis JTI replay, web distributed rate limits, v2.5.1 CR follow-ups (#245–#249), registry signed-manifest/400 fixes. See [CHANGELOG](CHANGELOG.md#252---2026-07-08), [PRD](product/prd/prd-v2.5.2-security-follow-up.md), and [Migration (v2.5.1 → v2.5.2)](docs/migration.md#upgrading-from-v251) |
| **v2.5.1** | **Code quality patch** — behavior-preserving refactor (transport/server, client, websocket, SQLite storage, auth, integrations) + six correctness/security fixes (atomic `revoke_cascade`, `usage_events` DDL, unified Host-JWT verifier, **WS now enforces OAuth2**, OpenAPI handler cleanup, client `correlation_id` binding). Deprecated import paths removed in v2.6.0. See [CHANGELOG](CHANGELOG.md#251---2026-06-25) and [Migration (v2.5.0 → v2.5.1)](docs/migration.md#upgrading-from-v250-to-v251) |
| **v2.5.0.1** | **Compliance publish** — **[GitHub Release `v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1)** · PyPI **`asap-compliance` 1.3.0** (`mcp-auth-bridge` profile; requires `asap-protocol>=2.5.0`). No `asap-protocol` API change. See [CHANGELOG](CHANGELOG.md#2501---2026-06-24) |
| **v2.5.0** | **MCP Auth Bridge** — **[GitHub Release `v2.5.0`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0)** · opt-in `protect_server` for stdio MCP; Agent JWT + capability grants; reference example `examples/mcp_auth_bridge/`. See [CHANGELOG](CHANGELOG.md#250---2026-06-24) and [Migration (v2.4.1 → v2.5.0)](docs/migration.md#upgrading-from-v241-to-v250) |
| **v2.4.1** | **Security hardening** — OAuth2 `iss`/`aud`, fail-closed identity binding, web SSRF/redirect fixes, dependency bumps. See [CHANGELOG](CHANGELOG.md#241---2026-06-14) and [Migration (v2.4.0 → v2.4.1)](docs/migration.md#upgrading-from-v240-to-v241) |
| **v2.4.0** | **Edge-AI discovery** — optional `hardware` / `inference` manifest fields, registry mirror, marketplace filters, **`@asap-protocol/client@2.4.0`**, ShellClaw onboarding docs. See [CHANGELOG](CHANGELOG.md#240---2026-05-24) and [Migration (v2.3.x → v2.4.0)](docs/migration.md#upgrading-from-v23x-to-v240) |
| **v2.3.1** | **npm TS patch** — **[GitHub Release `v2.3.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.3.1)** · **`@asap-protocol/mastra`**, **`@asap-protocol/openai-agents`**, **`@asap-protocol/client@2.3.1`** (additive adapter exports). Python **2.3.0** unchanged. See [CHANGELOG](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md#231---2026-05-20) and [Migration (v2.3.0 → v2.3.1)](https://github.com/asap-protocol/asap-protocol/blob/main/docs/migration.md#upgrading-from-v230-to-v231) |
| **v2.3.0** | **OpenAPI Adapter** (`[openapi]`) · **TypeScript client** (`@asap-protocol/client`) · **Auto-Registration** · **Capability escalation** · **ASAP HTTP challenge** — see [CHANGELOG](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md#230---2026-05-04) and [Migration](https://github.com/asap-protocol/asap-protocol/blob/main/docs/migration.md#upgrading-from-v22x-to-v230) |
| **v2.2.1** | Opt-in **WebAuthn** (`asap-protocol[webauthn]`) · `asap compliance-check` & `asap audit export` · stricter `ResolvedAgent.run()` · `AuditChainBroken` · [pinned security deps](https://github.com/asap-protocol/asap-protocol/blob/main/SECURITY.md#dependency-policy) |
| **v2.2** | Per-runtime identity & capability auth · SSE `POST /asap/stream` · `ASAP-Version` · JSON-RPC batch · tamper-evident audit · async state stores · Compliance Harness v2 |
| **v2.1.1** | Patch: JWT allowlist · SQLite async bridge · optional Redis rate limits · web SSRF hardening |
| **v2.1** | `MarketClient` · framework extras (LangChain, CrewAI, LlamaIndex, …) · registry UX |
| **v2.0** | Marketplace web app · Lite Registry (GitHub Pages) · IssueOps · OAuth · verification flow |
| **v1.3** | `asap delegation create` / `revoke` |
| **v1.2** | Ed25519 manifests · trust levels · optional mTLS · [Compliance Harness](https://github.com/asap-protocol/asap-protocol/blob/main/asap-compliance/README.md) |
| **v1.1** | OAuth2 · WebSocket · discovery (well-known + Lite Registry) · SQLite state · webhooks |

## 🔭 What's Next?

The [agentic marketplace](https://asap-protocol.com/) and Lite Registry are live. The **v2.5.x train** focuses on interop and adoption:

- **Formal Spec & Interop** — RFC, introspection, privacy (PRD [prd-v2.5.5-formal-spec-interop.md](product/prd/prd-v2.5.5-formal-spec-interop.md); library **2.5.5** is the security/quality patch above)
- **`@asap-protocol/mcp-auth`** (npm) — HTTP/SSE MCP middleware
- **Formal spec track** — introspection, privacy, cross-protocol interop on the path to v3.0 economy

See the [v2.5 roadmap PRD](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-v2.5-roadmap.md) and [ADR index](https://github.com/asap-protocol/asap-protocol/blob/main/product/decision-records/README.md).

## Contributing

**Community feedback and contributions are essential** for ASAP Protocol's evolution. We're working on improvements and your input helps shape the future of the protocol. 

Every contribution, from bug reports to feature suggestions, documentation improvements and code contributions, makes a real difference.

Check out our [contributing guidelines](https://github.com/asap-protocol/asap-protocol/blob/main/CONTRIBUTING.md) to get started. It's easier than you think! 🚀

## Contact

| Channel | Use for |
| --- | --- |
| [GitHub Discussions](https://github.com/asap-protocol/asap-protocol/discussions) or [Issues](https://github.com/asap-protocol/asap-protocol/issues) | Public questions, bugs, and feature ideas |
| [info@asap-protocol.com](mailto:info@asap-protocol.com) | Private coordination — protocol, marketplace, partnerships, press |
| [SECURITY.md](SECURITY.md) | Security vulnerabilities only — **do not** use email or public issues |

## Privacy

See [PRIVACY.md](PRIVACY.md) for how the public site and maintainer telemetry use aggregate metrics (including Vercel Web Analytics) without collecting agent IDs or end-user PII in repository outputs.

## License

This project is licensed under the Apache 2.0 License - see the [license](https://github.com/asap-protocol/asap-protocol/blob/main/LICENSE) file for details.

---

**Built with [Cursor](https://cursor.com/)**, with Composer, Grok, Opus, Gemini, Kimi and more.
