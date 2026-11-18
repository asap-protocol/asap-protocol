# Workflow connectors (OpenAPI → ASAP)

> **Support tier:** Maintained (Tier 2) — tested in CI; security fixes best effort. See [support tiers](./support-tiers.md).

Map **n8n / Activepieces-style workflow HTTP APIs** into ASAP **capabilities** by reusing the existing Python **OpenAPI adapter** (`asap.adapters.openapi`). Each OpenAPI `operationId` becomes a manifest **skill id**; invocations proxy to the upstream workflow host.

**Stack:** OpenAPI 3.0/3.1 fragment or full spec → `create_from_openapi` → `create_app` (same pattern as the PetStore demo). **No dedicated workflow adapter package.**

**Status:** Maintained integration guide (Adapter Lab II). Also listed under MkDocs **Integrations** and on the [docs home](../index.md).

## Purpose

Workflow platforms expose REST (or webhook) surfaces such as “list workflows”, “get one”, and “trigger a run”. ASAP agents need those actions as **discoverable skills** with JSON Schema inputs/outputs and a standard `task.request` path.

This integration:

1. Takes a curated OpenAPI document that describes the workflow HTTP API.
2. Maps selected operations to ASAP skills (`operationId` → skill id).
3. Serves a normal ASAP agent (`/.well-known/asap/manifest.json` + JSON-RPC `/asap`).
4. Proxies each skill invocation to the upstream workflow base URL.

**Contrast with Lab I framework adapters:** [Mastra](./mastra.md) and [OpenAI Agents](./openai-agents.md) wrap ASAP capabilities **as tools inside** a TypeScript agent runtime. Workflow connectors do the opposite direction: they turn an **external HTTP workflow API** into ASAP capabilities via OpenAPI, without a new adapter package.

## Requirements

| Runtime / tool | Version / note |
|----------------|----------------|
| Python | **3.13+** |
| `asap-protocol[openapi]` | OpenAPI extra (`openapi-pydantic`, shared `httpx` client) |
| Upstream | Mock `httpx` transport for CI / happy path, or a live workflow HTTP base URL |

## Capability mapping

The OpenAPI mapper uses each operation’s **`operationId`** as the ASAP **skill id** when present. The Lab II prototype fragment maps three workflow actions:

| Workflow action | HTTP | OpenAPI `operationId` / skill id |
|-----------------|------|----------------------------------|
| List workflows | `GET /workflows` | `listWorkflows` |
| Get one workflow | `GET /workflows/{workflowId}` | `getWorkflow` |
| Trigger / execute a run | `POST /workflows/{workflowId}/trigger` | `triggerWorkflow` |

Path and body parameters become the skill **input schema**; successful `200`/`201` `application/json` responses become the **output schema**. Details: [OpenAPI adapter](../adapters/openapi.md) (`map_openapi_to_capabilities`).

### Missing `operationId`

If an operation omits `operationId`, the mapper falls back to a synthetic name derived from method + path (for example `get_workflows_workflowId`). Prefer stable, explicit `operationId` values so remote agents and approval maps stay readable.

## Discovery (well-known / manifest)

After `create_from_openapi` + `create_app`, the ASAP host advertises skills on the standard discovery endpoint:

```http
GET /.well-known/asap/manifest.json HTTP/1.1
Host: your-asap-host.example
```

A remote agent:

1. Fetches the manifest and reads `skills` (ids such as `listWorkflows`, `getWorkflow`, `triggerWorkflow`).
2. Invokes a skill with JSON-RPC `asap.send` / `task.request` and the chosen `skill_id`, same as any other ASAP capability.
3. The OpenAPI-backed handler proxies the call to the upstream workflow API.

The prototype uses manifest id `urn:asap:agent:openapi-workflow-connector-example`. See [Transport — discovery](../transport.md) for the well-known contract.

## Quick start (runnable example)

From the repository root:

```bash
uv sync --extra openapi
uv run python examples/workflow_asap_connector/main.py
```

Runs Compliance Harness v2 (expects score **1.0**), then invokes `listWorkflows` against a **mocked** upstream (no SaaS credentials). Spike notes and skill table: [`examples/workflow_asap_connector/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/workflow_asap_connector).

Optional live upstream (HTTPS recommended; keep secrets in env only):

```bash
uv run python examples/workflow_asap_connector/main.py --live-base-url https://your-host.example/api/v1
```

Equivalent: `ASAP_WORKFLOW_BASE_URL`. Placeholders live in `.env.example` — never commit real tokens.

Minimal wiring — keep the shared `httpx` client open for the full proxy lifetime
(building the bundle **and** serving/invoking the app). The full runnable path is
`examples/workflow_asap_connector/main.py`.

```python
import asyncio
from pathlib import Path

import httpx

from asap.adapters.openapi import create_from_openapi
from asap.transport.server import create_app


async def build_workflow_asap_app(fragment: Path) -> None:
    """Keep the shared httpx client open for the full proxy lifetime."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
        bundle = await create_from_openapi(
            spec_path=fragment,
            http_client=http,
            default_capabilities="all",
            manifest_id="urn:asap:agent:openapi-workflow-connector-example",
            asap_endpoint="https://your-asap-host.example/asap",
            # upstream_base_url="https://your-workflow-host.example/api/v1",
        )
        app = create_app(bundle.manifest, bundle.registry)
        # Serve or invoke `app` here while `http` remains open.


# asyncio.run(build_workflow_asap_app(Path("openapi-fragment.json")))
```

## Failure modes

| Condition | What happens | Guidance |
|-----------|--------------|----------|
| Upstream **4xx** | OpenAPI handler raises **`FatalError`** (`asap:adapters/openapi/upstream_client_error`) | Fix caller args / auth; do not retry blindly |
| Upstream **5xx** or connection failure | **`RecoverableError`** (`upstream_server_error` / `upstream_connection`) | Retry with backoff; check workflow host health |
| Missing / empty **`operationId`** | Skill id becomes method+path fallback | Add stable `operationId`s before publishing the manifest |
| Unknown skill id on invoke | **`UnknownOpenAPICapabilityError`** (fatal) | Align client `skill_id` with advertised manifest skills |
| **Mock vs live** | Default example uses `httpx.MockTransport`; `--live-base-url` / `ASAP_WORKFLOW_BASE_URL` hits a real host | Keep CI on mock; use live only for manual exploration |
| Upstream auth | Adapter does **not** auto-apply OpenAPI `securitySchemes` | Pass **`resolve_headers`** (Bearer / API key from env) — see [OpenAPI adapter](../adapters/openapi.md) |

Identity / approval for production: configure `FreshSessionConfig` and optional `approval_strength` as in the OpenAPI guide. The workflow example intentionally skips WebAuthn wiring for the offline happy path.

## Troubleshooting

| Symptom | What to verify |
|---------|----------------|
| Empty or unexpected skill list | Fragment paths included; `default_capabilities` not filtering them out |
| Relative `servers[0].url` with local spec | Pass **`upstream_base_url`** (absolute origin) |
| Live call returns 401/403 | `resolve_headers` / env token; HTTPS endpoint |
| Compliance score ≠ 1.0 | Same agent shape as PetStore: `create_app` + skills + `task.request` handler |

## Related

- [Automation connector security](../guides/automation-connector-security.md) — secrets, TLS, webhooks, rate limits, manifests (MCP **N/A** for this OpenAPI example)
- [OpenAPI adapter](../adapters/openapi.md) — mapper, upstream proxy, approval / headers
- [Mastra integration](./mastra.md) — Lab I: ASAP capabilities → framework tools
- [OpenAI Agents SDK integration](./openai-agents.md) — Lab I: ASAP capabilities → Agents SDK tools
- Runnable example: [`examples/workflow_asap_connector/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/workflow_asap_connector)
- Error codes: [Error handling](../error-handling.md)
