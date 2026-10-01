# ShellClaw marketplace registration (static manifest)

This guide answers upstream questions for [ShellClaw](https://github.com/adriannoes/shellclaw) v1.0 **Wave 6** (Lite Registry / marketplace listing). It documents how to register an agent whose manifest is hosted **statically** (for example GitHub Pages) with **no live ASAP HTTP endpoint** in v1.0.

For maintainer review of Verified badge requests, see [Registry verification review](registry-verification-review.md). For the bot PR path (`POST /registry/agents`), see [Lite Registry auto-registration](../registry/auto-registration.md).

## Upstream answers (ShellClaw §5)

### §5.1 Registry entry validity — **YES, with notes**

The literal ShellClaw v1.0 entry is valid for `RegistryEntry` and `scripts/validate_registry.py`. A copy lives in the repo as a test fixture:

- **Fixture:** [`tests/fixtures/registry/shellclaw-v1.0-entry.json`](https://github.com/asap-protocol/asap-protocol/blob/main/tests/fixtures/registry/shellclaw-v1.0-entry.json)
- **Test:** `test_shellclaw_v1_fixture_validates` in [`tests/discovery/test_registry.py`](https://github.com/asap-protocol/asap-protocol/blob/main/tests/discovery/test_registry.py)

| Field | Status |
|-------|--------|
| `id` | `urn:asap:agent:shellclaw` — valid URN |
| `category` | `Infrastructure` — canonical enum |
| `tags` | Valid; do **not** submit `self-signed` manually (see below) |
| `built_with` | `Other` — valid today (`ShellClaw` optional in Issue template as of v2.4) |
| `verification` | `null` — valid |
| `online_check` | `false` — supported |
| `endpoints.http` + `endpoints.manifest` | Valid map keys per ADR-15 |

**Before public listing:** replace placeholder `https://shellclaw.example.com/asap` with a real URL when you have one. With `online_check: false`, IssueOps registration is acceptable while the HTTP endpoint is still a placeholder.

**Auto-registration:** `POST /registry/agents` runs Compliance Harness v2 against a **reachable** manifest URL and typically expects a live agent surface. ShellClaw v1.0 should use **IssueOps** (registration issue → maintainer PR) until a live tunnel exists (v1.0.1).

### §5.2 `built_with` enum — **optional**

`Other` is sufficient for v1.0. Adding `ShellClaw` to the registration issue dropdown improves Browse/filter discoverability only.

### §5.3 Trust label — **unchanged**

Manifest `signature.trust_level` and marketplace trust labeling use **`"self-signed"`** (hyphen). This matches `TrustLevel.SELF_SIGNED` in `asap.crypto.trust_levels`.

### §5.4 Static manifest hosting — **accepted**

`online_check: false` tells clients and the web UI to **skip reachability pings** for `endpoints.http`. Listing is **not rejected** because the agent has no live `POST /asap` endpoint in v1.0.

See [Browse and marketplace UI](#browse-and-marketplace-ui-spot-check) below for per-surface behavior.

### §5.5 Manifest URL path — **accepted on static hosts**

`endpoints.manifest` may point to a static URL such as:

`https://adriannoes.github.io/shellclaw/manifest.json`

The `/.well-known/asap/manifest.json` convention applies on the **agent’s own domain** when serving a live agent. It is **not required** on third-party static hosts (GitHub Pages often avoids dot-leading path segments).

---

## Browse and marketplace UI (spot-check)

Read-only audit of `apps/web` (2026-05-24). Seeded registry entries already use `online_check: false` (`apps/web/public/registry.json`); Browse lists them without error.

| Surface | `online_check: false` behavior |
|---------|------------------------------|
| **Browse** (`/browse`, `browse-content.tsx`) | Agent appears in the grid like any other listing. Only **revoked** URNs are removed (`browse/page.tsx`). Filters (search, category, tags, skills, SLA, auth) do **not** read `online_check`. **No** reachability probe on load. **No** Online/Offline/Demo badge on cards (`agent-card.tsx`). |
| **Agent detail** (`/agents/[id]`, `agent-detail-client.tsx`) | `AgentStatusBadge` uses `skipReachabilityCheck={agent.online_check === false}` → muted **Demo** badge. Does **not** ping `/api/proxy/check` and does **not** show **Offline** for an unreachable placeholder `endpoints.http`. |
| **Dashboard** (`/dashboard`, `dashboard-client.tsx`) | Same **Demo** badge on cards when `online_check === false`. |
| **Connect** | Endpoint URL is still shown; invoking the agent may fail until a live ASAP endpoint exists (expected for v1.0 static-only). |

**Summary:** Browse does **not** hard-fail or hide static-only agents. There is no red **Offline** badge on Browse cards; reachability UX is on the detail/dashboard surfaces only, and `online_check: false` avoids labeling those agents offline.

---

## Recommended registration path (v1.0)

| Path | Static manifest only | Notes |
|------|----------------------|--------|
| **IssueOps** ([Register Agent](https://github.com/asap-protocol/asap-protocol/issues/new?template=register_agent.yml)) | **Recommended** | Maintainer validates JSON; no live endpoint required when `online_check: false` |
| **Auto-registration** (`POST /registry/agents`) | **Not recommended** | Harness expects reachable manifest/agent surface |

---

## Authoring `registry.json` fields

### `online_check: false`

Set when the manifest is served from static hosting and there is **no** live ASAP JSON-RPC endpoint yet. Registry validators and the TypeScript/Python schemas accept `false`. Seeded demo agents in `registry.json` use the same pattern.

### `endpoints.manifest` on `*.github.io`

Use the HTTPS URL where the **signed** manifest JSON is published. ShellClaw v1.0 example:

```json
"endpoints": {
  "http": "https://shellclaw.example.com/asap",
  "manifest": "https://adriannoes.github.io/shellclaw/manifest.json"
}
```

### Placeholder `endpoints.http`

A placeholder ASAP URL is allowed for IssueOps while the tunnel ships in a later release. Update `endpoints.http` and manifest `endpoints.asap` when the live endpoint is available; consider setting `online_check: true` (or omitting it) once you want Online/Offline checks in the UI.

### Do not put `self-signed` in `tags`

Submitters must not add the trust label to `tags`. Auto-registration and IssueOps processing add it via `asap.registry.anti_spam` (`TRUST_LEVEL_SELF_SIGNED`).

### `verification`

Use `null` (or omit) for new self-signed listings. Verified badge promotion is a separate [verification request](registry-verification-review.md) flow.

---

## Example entry (ShellClaw v1.0)

```json
{
  "id": "urn:asap:agent:shellclaw",
  "name": "ShellClaw",
  "description": "The first C-native edge-AI-capable ASAP agent. Runs Phi-3-mini locally on NVIDIA Jetson Orin Nano Super via CUDA, exposes GPIO and I2C primitives on the 40-pin header as LLM-callable tools, and participates in the ASAP ecosystem with Ed25519-signed manifests.",
  "endpoints": {
    "http": "https://shellclaw.example.com/asap",
    "manifest": "https://adriannoes.github.io/shellclaw/manifest.json"
  },
  "skills": ["assistant", "edge_briefing", "server_admin", "gpio_control"],
  "category": "Infrastructure",
  "tags": ["cuda", "edge-ai", "hardware", "jetson", "local-inference"],
  "asap_version": "2.1.0",
  "repository_url": "https://github.com/adriannoes/shellclaw",
  "documentation_url": "https://github.com/adriannoes/shellclaw#readme",
  "built_with": "Other",
  "verification": null,
  "online_check": false
}
```

---

## Validate locally

**Single entry (Pydantic):**

```bash
PYTHONPATH=src uv run python -c "
import json
from pathlib import Path
from asap.discovery.registry import RegistryEntry
p = Path('tests/fixtures/registry/shellclaw-v1.0-entry.json')
RegistryEntry.model_validate(json.loads(p.read_text()))
print('OK')
"
```

Validates that the fixture matches `RegistryEntry`.

**IssueOps dry-run** (`validate_registry.py` agents-array format):

```bash
uv run python scripts/validate_registry.py --allow-agents-array tests/fixtures/registry/shellclaw-v1.0-agents-array.json
```

Same validation path as CI uses for `registry.json` agent objects.

**Full registry file** (when embedded in `registry.json`):

```bash
uv run python scripts/validate_registry.py path/to/registry.json
```

**Tests:**

```bash
uv run pytest tests/discovery/test_registry.py -k shellclaw -v
```

Runs the ShellClaw fixture regression test.

---

## ShellClaw handoff (Wave 6.2 IssueOps)

Copy into the shellclaw planning issue or PR when starting marketplace registration:

```text
ASAP upstream §5 answered (v2.4 S0): https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/shellclaw-registry.md

- §5.1 Registry entry §4: YES (use IssueOps; online_check: false; do not tag self-signed)
- §5.2 built_with: ShellClaw added to issue template; Other still valid
- §5.3 trust_level "self-signed": unchanged
- §5.4 static manifest + no live endpoint v1.0: accepted
- §5.5 manifest on *.github.io without .well-known: accepted

Open Register Agent issue with literal JSON from guide / tests/fixtures/registry/shellclaw-v1.0-entry.json
```

---

## Related

- [Identity signing](identity-signing.md) — Ed25519 manifests and `trust_level`
- [Compliance testing](compliance-testing.md) — Harness expectations when a live endpoint exists
- [Raw Fetch](../raw-fetch.md) — consume `registry.json` without Python
