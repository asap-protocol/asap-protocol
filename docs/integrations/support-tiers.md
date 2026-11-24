# Integration support tiers

Every ASAP integration, adapter, and package has a support tier. The tier tells you what the maintainers promise: how fast security fixes ship, what CI tests, and how much notice you get before a breaking change or a removal.

How releases carry these fixes is set by the [release and versioning policy](../maintainers/release-policy.md).

## Tiers

| Tier | Name | Commitments |
|------|------|-------------|
| **Tier 1** | Core | Security fixes on the latest minor line, on the security patch cadence (a hotfix when an advisory is exploitable). CI-gated: changes merge only with green CI. Breaking changes only in a minor release, after a deprecation notice. |
| **Tier 2** | Maintained | Tested in CI. Security fixes best effort, in the next maintenance patch. |
| **Tier 3** | Experimental | No compatibility promise. May be removed in a minor release, after one release of deprecation notice. |

## Classification

| Integration | Package / module | Tier | CI coverage | Notes |
|-------------|------------------|------|-------------|-------|
| Python SDK core | `asap-protocol`: `asap.models`, `asap.transport`, `asap.auth`, `asap.discovery`, `asap.state` | **1** | `ci.yml`: `test (python)`, `coverage` (85% floor), `security` | Required checks on `main` |
| MCP Auth Bridge | `asap.mcp.auth` (`protect_server`, `MCPAuthConfig`) | **1** | `ci.yml`: `test (python)` (`tests/adapters/mcp/`, `serial_mcp` subprocess test); `starters-smoke.yml` | Guide: [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) |
| MCP stdio server and client | `asap.mcp` (`MCPServer`, `MCPClient`, `asap.mcp.protocol`) | **1** | `ci.yml`: `test (python)` (`tests/mcp/`) | The Auth Bridge wraps this server, so it has the same tier. Guide: [MCP integration](../mcp-integration.md) |
| OpenAPI adapter | `asap.adapters.openapi`, extra `[openapi]` | **1** | `ci.yml`: `test (python)` (`tests/adapters/openapi/`); `starters-smoke.yml` | Guide: [OpenAPI adapter](../adapters/openapi.md) |
| TypeScript client | `@asap-protocol/client` (`packages/typescript/client`) | **1** | `typescript-sdk.yml` (lint, typecheck, test, tree-shake); `starters-smoke.yml` | Includes the LLM adapter subpaths `adapters/vercel-ai`, `adapters/openai`, `adapters/anthropic`. Guide: [TypeScript client](../sdks/typescript.md) |
| Compliance harness | `asap-compliance` (`asap-compliance/`) | **1** | `ci.yml`: `test (python)` (`asap-compliance/tests/`, example agent baseline) | Guide: [Compliance testing](../guides/compliance-testing.md) |
| Workflow connectors | `examples/workflow_asap_connector/` on top of `asap.adapters.openapi` | **2** | `ci.yml`: `test (python)` (`tests/examples/test_workflow_asap_connector.py`) | Guide and example only; no separate package. Guide: [Workflow connectors](./workflow-connectors.md) |
| Vercel AI SDK router (Python) | `asap.integrations.vercel_ai` (`create_asap_tools_router`) | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_vercel_ai.py`) | No auth by default; pass `api_key_header` or mount behind your own auth. Guide: [Vercel AI SDK](../guides/vercel-ai-sdk.md) |
| OpenClaw bridge (Python) | `asap.integrations.openclaw`, extra `[openclaw]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_openclaw.py`) | Guide: [OpenClaw integration](../guides/openclaw-integration.md) |
| A2H (Agent-to-Human) | `asap.integrations.a2h` (`A2HClient`, `A2HApprovalProvider`) | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_a2h.py`, `test_a2h_e2e_simulation.py`) | Tracks the external Agent2Human spec. See [Capability escalation](../capabilities/escalation.md) |
| LangChain | `asap.integrations.langchain` (`LangChainAsapTool`), extra `[langchain]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_langchain.py`; CI installs all extras) | Documented in the README integrations table and module docstrings; no docs page yet |
| CrewAI | `asap.integrations.crewai` (`CrewAIAsapTool`), extra `[crewai]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_crewai.py`) | The `security` job audits without this extra: its dependency graph has upstream advisories with no fixed release. See [SECURITY.md](https://github.com/asap-protocol/asap-protocol/blob/main/SECURITY.md) |
| LlamaIndex | `asap.integrations.llamaindex` (`LlamaIndexAsapTool`), extra `[llamaindex]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_llamaindex.py`) | Same audit exclusion as CrewAI |
| PydanticAI | `asap.integrations.pydanticai` (`asap_tool_for_urn`), extra `[pydanticai]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_pydanticai.py`) | Documented in the README integrations table and module docstrings |
| SmolAgents | `asap.integrations.smolagents` (`SmolAgentsAsapTool`), extra `[smolagents]` | **2** | `ci.yml`: `test (python)` (`tests/integrations/test_smolagents.py`) | `pip-audit` ignores two upstream advisories that have no fixed release yet |
| MCP registry server | `asap-mcp-server` (`asap.mcp.serve`), extra `[mcp]` | **2** | `ci.yml`: `test (python)` (`tests/mcp/test_serve.py`) | Console script; module docstring only, no docs page yet |
| Mastra | `@asap-protocol/mastra` (`packages/typescript/mastra`) | **3** | `mastra-ci.yml` (`@mastra/core` 1.5.0 and latest); not a required check | Promotion gate not met. Guide: [Mastra](./mastra.md) |
| OpenAI Agents SDK | `@asap-protocol/openai-agents` (`packages/typescript/openai-agents`) | **3** | `openai-agents-ci.yml` (`@openai/agents` 0.11.4 and latest); not a required check | Promotion gate not met; `@openai/agents` is pre-1.0. Guide: [OpenAI Agents SDK](./openai-agents.md) |
| Microsoft Agent Framework | Interop guide only | **3** | None | No ASAP package or sample. Guide: [Microsoft Agent Framework](./microsoft-agent-framework.md) |
| NeMo Agent Toolkit | Interop guide + `examples/nemo_agent_toolkit_asap/` | **3** | `ci.yml`: `test (python)` covers the ASAP side only (`tests/examples/test_nemo_agent_toolkit_asap.py`); the NAT import is skipped | Guide: [NeMo Agent Toolkit](./nemo-agent-toolkit.md) |
| OpenClaw skill (Node) | `asap-openclaw-skill` (`packages/asap-openclaw-skill`) | **3** | None (not in the pnpm workspace or any workflow) | Has local Vitest tests only |

CI workflows named above: [`ci.yml`](https://github.com/asap-protocol/asap-protocol/blob/main/.github/workflows/ci.yml), [`typescript-sdk.yml`](https://github.com/asap-protocol/asap-protocol/blob/main/.github/workflows/typescript-sdk.yml), [`starters-smoke.yml`](https://github.com/asap-protocol/asap-protocol/blob/main/.github/workflows/starters-smoke.yml), [`mastra-ci.yml`](https://github.com/asap-protocol/asap-protocol/blob/main/.github/workflows/mastra-ci.yml), [`openai-agents-ci.yml`](https://github.com/asap-protocol/asap-protocol/blob/main/.github/workflows/openai-agents-ci.yml).

Examples under `examples/` and demo apps under `apps/` are reference code. They have no tier and no compatibility promise.

## Promotion and demotion

**Tier 3 → Tier 2** needs both:

1. **Demand:** 3 or more `adapter-request` issues from people outside the maintainer team, **or** 1 partner commitment to use the integration.
2. **CI coverage:** the integration's tests run in CI on every pull request that touches it.

**Demotion or removal** (Tier 1 → 2, Tier 2 → 3, or removal from the repo) happens only in a minor release, after one release of deprecation notice. The notice goes in the [CHANGELOG](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md) and on the integration's guide.

Tier changes are recorded in the change log at the bottom of this page.

## Request an integration or a promotion

Open an [`adapter-request` issue](https://github.com/asap-protocol/asap-protocol/issues/new?template=adapter-request.yml). Give the framework name and your use case, and say whether you can test a pre-release or integrate at your company. Use the same form to ask for a Tier 3 integration to move to Tier 2.

## Change log

| Date | Change |
|------|--------|
| 2026-11-18 | Initial tiers and classification. |
| 2026-11-23 | MCP Auth Bridge row: v2.6.0 removes the old import path, so the row names only `asap.mcp.auth`. The tier does not change. |
