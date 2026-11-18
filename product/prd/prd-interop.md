# PRD: ASAP Protocol v2.6.0 — Interop

> **Product Requirements Document**
>
> **Version**: 2.6.0 (target minor)
> **Status**: PLANNED — **next** product train. Kickoff after Phase 0 Reset ([prd-roadmap.md §2](./prd-roadmap.md#2-roadmap)). Decisions D1–D9 are **TBD at kickoff**.
> **Created**: 2026-11-18 — split from `prd-v2.5.5-formal-spec-interop.md` §3.4 (now [prd-spec-profile.md](./prd-spec-profile.md)) and the v2.5.0 [`@asap-protocol/mcp-auth` backlog](../../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md)
> **Last Updated**: 2026-11-18
> **Parent**: [prd-roadmap.md](./prd-roadmap.md)
> **Predecessor**: [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md) (last planned v2.5.x train; library line closed at **2.5.11**)
> **Successor**: [prd-spec-profile.md](./prd-spec-profile.md) (v2.7.0)
> **Tasks**: create `engineering/tasks/v2.6.0/` at kickoff

---

## 1. Purpose

Agents already speak A2A and MCP. v2.6.0 makes ASAP's identity, capability grants, and compliance checks work **inside** those protocols, so a team does not have to leave A2A or MCP to get them.

Three gaps block that today:

1. **Discovery.** An A2A v1.0 Agent Card and an ASAP manifest describe the same agent in two formats. Only a docs-only field sketch connects them. Nothing verifies a signed card.
2. **Tokens.** ASAP accepts its own Host/Agent JWTs. It does not turn an external agent token, such as an MCP OAuth 2.1 bearer token, into an ASAP agent session with grants.
3. **MCP over HTTP.** The MCP Auth Bridge (v2.5.0) protects **stdio** servers in Python. HTTP MCP servers have no ASAP middleware. `@asap-protocol/mcp-auth` has waited in the backlog since v2.5.0.

The spec profile (v2.7.0) will describe these layers. They ship first so the spec documents shipped behavior ([prd-roadmap.md §2](./prd-roadmap.md#2-roadmap)).

---

## 2. Goals

| Goal | Metric (falsifiable) | Priority |
|------|----------------------|----------|
| A2A Agent Card bridge | A signed A2A v1.0 Agent Card verifies and maps to a valid ASAP manifest, and back; the D1 A2A↔ASAP flow passes end to end with compliance checks | P1 |
| External tokens → ASAP sessions | A bearer token from a configured issuer (including an MCP OAuth 2.1 access token) becomes an ASAP agent session with explicit grants; tokens from unconfigured issuers are rejected | P1 |
| `@asap-protocol/mcp-auth` | Published on npm; protects an HTTP MCP server; returns the same four `asap:*` error codes as the Python bridge | P1 |
| Discovery bridge | ASAP well-known manifest is served beside the A2A Agent Card on the same origin | P2 |
| Interoperability guide | Published in `docs/`; walks through both exit-criteria flows | MUST |
| Deprecation housekeeping | All six import paths that `CHANGELOG.md` `[2.5.1]` marks "remove in v2.6.0" are removed, including `asap.cli._compat` ([#275](https://github.com/asap-protocol/asap-protocol/issues/275)); `docs/migration.md` lists them with replacements | MUST |

---

## 3. Baseline inventory (do not rebuild)

| Area | Already present | Gap for this PRD |
|------|-----------------|------------------|
| Agent Card → manifest mapping | [docs/migration.md — Agent Card to Manifest](../../docs/migration.md#agent-card-to-manifest); docs-only sketch in [docs/integrations/nemo-agent-toolkit.md](../../docs/integrations/nemo-agent-toolkit.md) (Lab II) | No runtime or gateway bridge; no reverse mapping; no signed-card verification |
| External OAuth / OIDC validation | `asap.auth.oidc` (discovery), `asap.auth.jwks` (JWKS signature validation, key rotation), `asap.auth.introspection` (RFC 7662 for opaque tokens), `asap.auth.middleware` | No mapping from an external token to an ASAP agent session and grants |
| MCP auth (Python) | `asap.mcp.auth` stdio bridge (v2.5.0): `_meta.asap_agent_jwt`, error codes `asap:auth_required`, `asap:invalid_token`, `asap:capability_denied`, `asap:constraint_violation` | No HTTP transport; no TypeScript package |
| TypeScript packages | `@asap-protocol/client` 2.4.1 mints Host/Agent JWTs ([spike §2.3](../../engineering/tasks/v2.5.0/typescript-mcp-auth-spike.md)) | No public `verifyAgentJwt()`; no `packages/typescript/mcp-auth/` |
| npm publish CI | `.github/workflows/publish-typescript.yml` | Triggers only on `v2.3.*` / `v2.4.*` tags and three packages |
| Compliance | `asap-compliance` validators: handshake, schema, state, SLA, MCP auth (stdio) | No checks for the A2A bridge, external tokens, or HTTP MCP |

---

## 4. Functional requirements

IDs COMPAT-001..005 and MCP-TS-001..003 keep their original numbers so history stays traceable. COMPAT-001..005 came from `prd-v2.5.5-formal-spec-interop.md` §3.4. MCP-TS-001..003 came from [prd-v2.5.0-mcp-auth-bridge.md §5.4](./prd-v2.5.0-mcp-auth-bridge.md#54-typescript-should). New IDs start at COMPAT-006.

### 4.1 A2A Agent Card bridge (P1)

| ID | Requirement | Priority | Was | Acceptance (falsifiable) |
|----|-------------|----------|-----|--------------------------|
| **COMPAT-005** | A2A v1.0 Agent Card ↔ ASAP manifest mapping, both directions. Shape set by D1 (runtime adapter or documented gateway). Inputs: the field sketch in [docs/integrations/nemo-agent-toolkit.md](../../docs/integrations/nemo-agent-toolkit.md#agent-card--manifest-sketch) (which cites this PRD) and [docs/migration.md](../../docs/migration.md#agent-card-to-manifest). | MUST | SHOULD (P3) | Fixture tests: card → manifest → card keeps every mapped field; the guide lists every unmapped field and why |
| **COMPAT-006** | Verify signed Agent Cards before mapping. Policy (D3) decides if unsigned cards are rejected. | MUST | new (part of COMPAT-005 scope) | Tests: valid signature passes; tampered card, unknown key, and unsigned card under a require-signature policy each fail with a typed error |

### 4.2 External agent tokens (P1)

| ID | Requirement | Priority | Was | Acceptance (falsifiable) |
|----|-------------|----------|-----|--------------------------|
| **COMPAT-001** | Accept external agent tokens and turn them into ASAP agent sessions, including MCP OAuth 2.1 bearer tokens. Token types per D4. | MUST | SHOULD (P3) | Tests: token from an allowlisted issuer with the right audience → session; wrong issuer, wrong audience, expired, or replayed token → rejected |
| **COMPAT-002** | Translate external grants (scopes or claims) into ASAP capability grants through explicit configuration. Deny by default. | MUST | SHOULD (P3) | Tests: unmapped scope grants nothing; mapped scope grants only the configured capability and constraints |

### 4.3 `@asap-protocol/mcp-auth` for HTTP MCP servers (P1)

| ID | Requirement | Priority | Was | Acceptance (falsifiable) |
|----|-------------|----------|-----|--------------------------|
| **MCP-TS-001** | Publish `@asap-protocol/mcp-auth` with `createMcpAuthMiddleware(config)` for HTTP MCP servers (transports per D6) | MUST | SHOULD (v2.5.0 backlog) | Package on npm; example HTTP MCP server protected end to end |
| **MCP-TS-002** | Bearer extraction and the same `asap:*` error code mapping as Python | MUST | SHOULD | vitest: missing token, invalid token, capability denied, constraint violation, and success path match the Python codes |
| **MCP-TS-003** | Re-export types compatible with `@modelcontextprotocol/sdk` | MUST | SHOULD | Type-check of the example against the pinned SDK version passes |

### 4.4 Discovery bridge (P2)

| ID | Requirement | Priority | Was | Acceptance (falsifiable) |
|----|-------------|----------|-----|--------------------------|
| **COMPAT-003** | Serve the ASAP well-known manifest beside other protocol endpoints, at minimum the A2A Agent Card, from one config | SHOULD | SHOULD (P3) | Integration test: both documents served from one origin and describe the same agent identity |

### 4.5 Guide and compliance (MUST)

| ID | Requirement | Priority | Was | Acceptance (falsifiable) |
|----|-------------|----------|-----|--------------------------|
| **COMPAT-004** | Interoperability guide for multi-protocol environments (A2A + MCP + ASAP) | MUST | MUST | Guide in `docs/` and `mkdocs.yml`; covers both exit-criteria flows with runnable commands; follows the wording rule in [prd-roadmap.md §4.3](./prd-roadmap.md#43-wording-rule) |
| **COMPAT-007** | `asap-compliance` checks for both exit-criteria flows | MUST | new | Harness runs against a remote A2A-bridged agent and a remote HTTP MCP server; each flow has pass and fail cases |

### 4.6 Housekeeping — deprecations promised for v2.6.0

v2.5.1 deprecated six import paths and promised to remove them in v2.6.0. Sources: `CHANGELOG.md` `[2.5.1]` "Deprecated (remove in v2.6.0)" and [docs/migration.md — Upgrading from v2.5.0 to v2.5.1](../../docs/migration.md#upgrading-from-v250-to-v251). [docs/integrations/support-tiers.md](../../docs/integrations/support-tiers.md) also states that `asap.adapters.mcp` is a shim until v2.6.0. Tier 1 breaking changes ship only in a minor release after a deprecation notice; these six got that notice in v2.5.1. None of them is a protocol or wire change, so a minor is the right release type ([release policy](../../docs/maintainers/release-policy.md)).

| ID | Deprecated path | Replacement | Priority | Acceptance (falsifiable) |
|----|-----------------|-------------|----------|--------------------------|
| **HK-001** | `asap.cli._compat` ([#275](https://github.com/asap-protocol/asap-protocol/issues/275); [task file](../../engineering/tasks/deferred/issue-275-cli-compat-removal-v2.6.0.md)) | `asap.cli.schemas`, `asap.schemas`, or `asap.cli.repl` | MUST | `src/asap/cli/_compat.py` deleted; zero in-repo hits for `asap.cli._compat` |
| **HK-002** | `asap.transport.websocket` | `asap.transport.ws` | MUST | `src/asap/transport/websocket.py` deleted; zero in-repo imports of the old path |
| **HK-003** | `asap.adapters.mcp` | `asap.mcp.auth` | MUST | `src/asap/adapters/mcp/` shim deleted; zero in-repo imports; MCP Auth Bridge guide and support-tiers row no longer mention the legacy path |
| **HK-004** | `RemoteFatalRPCError` / `RemoteRecoverableRPCError` | `RemoteRPCError` + `is_recoverable` | MUST | Both classes removed from `asap.errors`; no code imports or raises them |
| **HK-005** | `metering_storage_adapter` | `MeteringStorageBridge` | MUST | Function removed from `asap.economics.storage`; zero in-repo callers |
| **HK-006** | `FRAME_ENCODING_BINARY` literal (kept on the `asap.transport.websocket` shim for import compatibility; binary framing is already gone) | None — JSON text frames only | MUST | Literal removed with HK-002; zero in-repo references |
| **HK-007** | `docs/migration.md` gets an "Upgrading from v2.5.x to v2.6.0" section that lists HK-001..HK-006 with before/after imports | — | MUST | Section present and linked from `CHANGELOG.md` `[2.6.0]`, which carries a "Removed" entry for each path |

### 4.7 Dependencies

| Requirement | Depends on |
|-------------|------------|
| COMPAT-005 / COMPAT-006 | D1, D2, D3; external spec checks in §6 |
| COMPAT-001 / COMPAT-002 | D4, D5; existing `asap.auth` OIDC / JWKS / introspection modules |
| MCP-TS-001..003 | D6, D7, D8; public JWT verification in TypeScript |
| COMPAT-003 | COMPAT-005 (card generated from the manifest) |
| COMPAT-007 | Flows chosen in D1 and D6 |
| COMPAT-004 | All P1 items (the guide documents shipped behavior) |
| HK-006 | HK-002 (the literal lives on the `asap.transport.websocket` shim) |
| HK-007 | HK-001..HK-006 |

---

## 5. Locked decisions (D1–D9)

> **All TBD at kickoff.** Fill the "Locked value" column during S0 scope lock, then set status to READY FOR KICKOFF. Same pattern as [prd-v2.5.4 §3](./prd-v2.5.4-distribution-loop.md#3-locked-decisions-d1d5).

| ID | Decision | Options to evaluate | Locked value |
|----|----------|---------------------|--------------|
| **D1** | A2A bridge shape and the exit-criteria A2A flow | (a) runtime adapter in `asap-protocol` (for example `asap.adapters.a2a`); (b) documented gateway pattern plus mapping library. Flow candidates: **inbound** — an ASAP agent publishes a card generated from its manifest, and an A2A client calls one skill under an ASAP grant; **outbound** — an ASAP consumer ingests a signed external card and calls the agent through the bridge | TBD |
| **D2** | A2A target and SDK | Which A2A v1.0 schema revision; depend on the A2A Python SDK (which version) or keep own Pydantic models | TBD |
| **D3** | Signed-card policy | Require signatures by default or opt-in; key discovery source (key reference in the card vs. pinned keys); trust store config | TBD |
| **D4** | Token types accepted (COMPAT-001) | MCP OAuth 2.1 access tokens as JWT (JWKS) and/or opaque (RFC 7662 introspection); tokens from A2A-declared security schemes; others | TBD |
| **D5** | Session and grant mapping | External subject → ephemeral ASAP session or registered Host/Agent; grant source = static config, approval flow, or both | TBD |
| **D6** | `@asap-protocol/mcp-auth` scope | ASAP Agent JWT only, or also external tokens via COMPAT-001; MCP HTTP transport(s) to support; where JWT verification lives (`@asap-protocol/client` or `mcp-auth`) | TBD |
| **D7** | Python HTTP MCP parity | Python `asap.mcp.auth` also gains HTTP in v2.6.0, or TypeScript only for this train | TBD |
| **D8** | npm version, publish, and tier | First version of `@asap-protocol/mcp-auth` (each package keeps its own version per the [release policy](../../docs/maintainers/release-policy.md)); extend `publish-typescript.yml` beyond `v2.3.*` / `v2.4.*` tags and add the fourth package; support tier at launch (the Python MCP Auth Bridge is Tier 1 in [support-tiers.md](../../docs/integrations/support-tiers.md)) | TBD |
| **D9** | Deprecation removals (HK-001..HK-007) | Confirm all six removals ship in v2.6.0 (default, matches the public promise). An extension is an exception: it needs a recorded reason and a new target in `CHANGELOG.md` `[2.6.0]` and in this PRD | TBD |

**Release shape (proposed, confirm at kickoff):** minor release, tag **`v2.6.0`**, PyPI `asap-protocol==2.6.0`, `asap-compliance` minor bump for COMPAT-007, npm per D8, `CHANGELOG.md` `[2.6.0]`, `docs/migration.md` v2.5.11 → v2.6.0 section.

---

## 6. External specs to verify at kickoff

This PRD does not assert details of external specs. Confirm each item against the source spec before D1–D6 lock, and record the spec version or revision in the guide.

| Item | What to confirm | Used by |
|------|-----------------|---------|
| A2A v1.0 Agent Card | Schema fields, well-known path (the NAT guide uses `/.well-known/agent-card.json`), declared security schemes | COMPAT-003, COMPAT-005, D2 |
| A2A signed Agent Cards | Signature format, canonicalization, key discovery | COMPAT-006, D3 |
| MCP authorization | Spec revision to target; OAuth 2.1 profile; protected-resource metadata; audience or resource binding rules | COMPAT-001, D4, D6 |
| MCP HTTP transport | Current transport name(s) and the middleware hooks in `@modelcontextprotocol/sdk` | MCP-TS-001, MCP-TS-003, D6 |
| IETF agent identity drafts (WIMSE/AIMS, OAuth agent drafts) | Track only; no MUST in this PRD depends on draft text | COMPAT-004 (informative) |

---

## 7. Security considerations

| Rule | Applies to |
|------|-----------|
| External tokens never get more than the configured grant mapping. Unmapped scopes grant nothing. | COMPAT-001, COMPAT-002 |
| Issuer allowlist and audience check are required. No "accept any issuer" mode outside tests. | COMPAT-001, MCP-TS-001 |
| Reuse the existing JTI replay cache and SSRF guards for every fetch of Agent Cards, JWKS, and metadata. | COMPAT-001, COMPAT-005, COMPAT-006 |
| A card that fails signature verification is never mapped or cached. | COMPAT-006 |
| HTTP MCP middleware returns `asap:*` errors without leaking token contents. | MCP-TS-002 |

Run the [security review skill](../../.cursor/skills/security-review/SKILL.md) on the bridge and token PRs before release.

---

## 8. Non-goals

| Item | Where / note |
|------|--------------|
| Replacing A2A or MCP | Never — ASAP layers on them ([prd-roadmap.md §4](./prd-roadmap.md#4-positioning)) |
| Full A2A client or server inside `asap-protocol` | Out; reuse the A2A SDK or a gateway (D1, D2) |
| Requiring the ASAP envelope for interop flows | Out; bridges meet A2A and MCP on their own wire formats |
| Formal spec text | [prd-spec-profile.md](./prd-spec-profile.md) (v2.7.0) |
| Capability-aware introspection endpoint (INTRO-*) | [prd-spec-profile.md](./prd-spec-profile.md) §3.2 |
| `@asap-protocol/openapi` (TSOA) | Default defer in [prd-spec-profile.md](./prd-spec-profile.md) §3.5 |
| Payments, AP2, settlement | Economy product track ([prd-v3.0-economy.md](./prd-v3.0-economy.md)) |
| gRPC binding, federated registry, schema negotiation runtime | [prd-roadmap.md §6](./prd-roadmap.md#6-non-goals-all-tracks-unless-a-track-prd-says-otherwise) |
| Promoting Tier 3 integrations | Promotion gate in [support-tiers.md](../../docs/integrations/support-tiers.md#promotion-and-demotion) |
| Pricing, GTM, adoption numbers in public copy | Never in public files |

---

## 9. Prerequisites

| Prerequisite | Kind | Source |
|--------------|------|--------|
| v2.5.x closed; release policy published | **Hard** (process) | Phase 0 — [prd-roadmap.md §2](./prd-roadmap.md#2-roadmap) |
| MCP Auth Bridge (Python, v2.5.0) error codes stable | **Hard** (MCP-TS-002 parity) | [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md) |
| Identity and capability model stable | **Hard** | v2.2+ |
| OIDC / JWKS / introspection modules in `asap.auth` | **Hard** (shipped) | §3 |
| External specs checked (§6) | **Hard** for COMPAT-001, COMPAT-005, COMPAT-006 | Kickoff S0 |
| 1–3 external design partners for the A2A or MCP-HTTP flow | **Soft** | Phase 0 |
| Weekly adoption telemetry baseline | **Soft** (decision-gate input, not a build gate) | Phase 0 — [telemetry runbook](../../docs/maintainers/telemetry.md) |

---

## 10. Exit criteria

v2.6.0 ships only when **one A2A↔ASAP flow and one MCP-HTTP↔ASAP flow work end to end, each with compliance checks**.

| Flow | Minimum end-to-end path | Proof |
|------|-------------------------|-------|
| A2A ↔ ASAP | The D1 flow: signed card verified (COMPAT-006) → mapped (COMPAT-005) → one capability call authorized against an ASAP grant (COMPAT-001/002 where a token is involved) | Runnable example + CI test + COMPAT-007 check |
| MCP-HTTP ↔ ASAP | MCP client → `tools/call` on an HTTP MCP server protected by `@asap-protocol/mcp-auth` → allowed call succeeds; missing token, invalid token, out-of-grant call, and constraint violation return the four `asap:*` codes | Runnable example + vitest + COMPAT-007 check |

---

## 11. Definition of Done

- [ ] D1–D9 locked in §5; status → READY FOR KICKOFF; `engineering/tasks/v2.6.0/` created
- [ ] §6 external spec items confirmed; versions recorded in the guide
- [ ] COMPAT-001, COMPAT-002, COMPAT-005, COMPAT-006 green
- [ ] MCP-TS-001..003 green; `@asap-protocol/mcp-auth` published on npm
- [ ] COMPAT-003 green, **or** explicit deferral recorded in [prd-roadmap.md](./prd-roadmap.md) (SHOULD)
- [ ] COMPAT-004 and COMPAT-007 green
- [ ] HK-001..HK-007 green (or a D9 exception recorded for a specific path)
- [ ] §10 exit-criteria flows run in CI
- [ ] §7 security rules verified; security review done on bridge and token PRs
- [ ] Release: tag `v2.6.0`, PyPI `asap-protocol==2.6.0`, `asap-compliance` bump, npm per D8, `CHANGELOG.md` `[2.6.0]`, migration section
- [ ] Public copy follows the wording rule and has no pricing, GTM, or adoption numbers
- [ ] §12 handoff inputs for the spec profile confirmed

---

## 12. Handoff inputs for v2.7.0 (Spec profile)

| Input | Spec use |
|-------|----------|
| Agent Card ↔ manifest mapping table (COMPAT-005) | Normative A2A profile section (SPEC-011) |
| Signed-card verification rules (COMPAT-006) | Security considerations (SPEC-006) |
| External-token → session and grant rules (COMPAT-001/002) | OAuth / MCP profile section (SPEC-011) |
| HTTP MCP middleware behavior and error codes (MCP-TS-*) | MCP section (SPEC-009) and error registry (SPEC-005) |
| COMPAT-007 compliance checks | Seed for the MUST → check traceability table (SPEC-010) |
| Interoperability guide (COMPAT-004) | Informative reference |

---

## 13. Related documents

- **Train index**: [prd-roadmap.md](./prd-roadmap.md)
- **Spec profile (successor, v2.7.0)**: [prd-spec-profile.md](./prd-spec-profile.md)
- **MCP Auth Bridge (v2.5.0)**: [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md)
- **`@asap-protocol/mcp-auth` backlog and spike**: [backlog](../../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md) · [spike](../../engineering/tasks/v2.5.0/typescript-mcp-auth-spike.md)
- **#275 task file**: [issue-275-cli-compat-removal-v2.6.0.md](../../engineering/tasks/deferred/issue-275-cli-compat-removal-v2.6.0.md)
- **Release policy**: [docs/maintainers/release-policy.md](../../docs/maintainers/release-policy.md)
- **Existing mapping docs**: [docs/migration.md](../../docs/migration.md#agent-card-to-manifest) · [NeMo Agent Toolkit guide](../../docs/integrations/nemo-agent-toolkit.md) · [MCP Auth Bridge guide](../../docs/adapters/mcp-auth-bridge.md)
- **Historical source**: [prd-v2.5.5-formal-spec-interop.md](./prd-v2.5.5-formal-spec-interop.md) (redirect stub)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-11-18 | **Created** (roadmap reset). COMPAT-001..005 moved from the Formal Spec PRD §3.4 and re-prioritized: COMPAT-005 (A2A Agent Card bridge) and COMPAT-001/002 (external tokens, incl. MCP OAuth 2.1) → P1 MUST; COMPAT-003 → P2; COMPAT-004 stays MUST. New COMPAT-006 (signed-card verification) and COMPAT-007 (compliance checks). MCP-TS-001..003 moved from the v2.5.0 backlog as P1 MUST. Housekeeping HK-001..HK-006 (the six import paths `CHANGELOG.md` `[2.5.1]` promised to remove in v2.6.0, incl. #275) and HK-007 (migration section). COMPAT-005 cites the NAT guide field sketch as input. D1–D9 TBD at kickoff. |
