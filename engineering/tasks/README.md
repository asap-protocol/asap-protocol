# Engineering Tasks (`engineering/tasks/`)

Sprint roadmaps and release checklists per protocol version. Pair each folder with the matching PRD in [product/prd/README.md](../../product/prd/README.md).

---

## Active / next

| Folder | PRD | Status |
|--------|-----|--------|
| _(none)_ — create [v2.5.5/](./v2.5.5/) at Formal Spec kickoff | [prd-v2.5.5-formal-spec-interop.md](../../product/prd/prd-v2.5.5-formal-spec-interop.md) | **NEXT** |

Library patches **v2.5.6**, **v2.5.7**, and **v2.5.8** shipped on `main` without an `engineering/tasks/v2.5.6/`, `v2.5.7/`, or `v2.5.8/` folder (release/docs only; see `CHANGELOG.md` `[2.5.6]` / `[2.5.7]` / `[2.5.8]`). The **v2.5.5/** task folder name is reserved for Formal Spec kickoff, not the latest PyPI version.

---

## Shipped (public)

| Folder | PRD | Shipped |
|--------|-----|---------|
| [v2.5.4/](./v2.5.4/tasks-v2.5.4-roadmap.md) | [prd-v2.5.4-distribution-loop.md](../../product/prd/prd-v2.5.4-distribution-loop.md) | 2026-07-18 — tag [`v2.5.4`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.4) |
| [v2.5.3/](./v2.5.3/tasks-v2.5.3-roadmap.md) | [prd-v2.5.3-adapter-lab-ii.md](../../product/prd/prd-v2.5.3-adapter-lab-ii.md) | 2026-07-16 — tag [`v2.5.3`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.3) |
| [v2.5.0/](./v2.5.0/tasks-v2.5.0-roadmap.md) | [prd-v2.5.0-mcp-auth-bridge.md](../../product/prd/prd-v2.5.0-mcp-auth-bridge.md) | 2026-06-24 — [release-checklist.md](./v2.5.0/release-checklist.md) |
| [v2.4.0/](./v2.4.0/tasks-v2.4.0-roadmap.md) | [prd-v2.4.0-edge-ai-discovery.md](../../product/prd/prd-v2.4.0-edge-ai-discovery.md) | 2026-05-24 |
| [v2.3.0/](./v2.3.0/tasks-v2.3.0-adoption-multiplier.md) | [prd-v2.3-scale.md](../../product/prd/prd-v2.3-scale.md) | 2026-05-04 |
| [v2.2.1/](./v2.2.1/tasks-v2.2.1-patch.md) | [prd-v2.2.1-patch.md](../../product/prd/prd-v2.2.1-patch.md) | 2026-04-21 |
| [v2.2.0/](./v2.2.0/tasks-v2.2.0-protocol-hardening.md) | [prd-v2.2-protocol-hardening.md](../../product/prd/prd-v2.2-protocol-hardening.md) | 2026-04-15 |
| [v2.1.0/](./v2.1.0/tasks-v2.1.0-roadmap.md) | [prd-v2.1-ecosystem.md](../../product/prd/prd-v2.1-ecosystem.md) | 2026-02-28 |
| [v2.0.0/](./v2.0.0/tasks-v2.0.0-roadmap.md) | [prd-v2.0-roadmap.md](../../product/prd/prd-v2.0-roadmap.md) | 2026-02-23 |
| v1.4.0 … v0.1.0 | matching `prd-v1*.md` | see folders |

---

## Shipped (private — local only, gitignored)

| Folder | PRD | Shipped |
|--------|-----|---------|
| `private/v2.5.1/` | code quality review patch (S0–S3 + release); Adapter Lab II deferred to v2.5.3 | 2026-06-25 |
| `private/v2.4.1/` | [prd-v2.4.1-security-hardening.md](../../product/prd/prd-v2.4.1-security-hardening.md) + `private/prd-v2.4.1-*` | 2026-06-14 |
| `private/v2.3.1/` | `private/prd-v2.3.1-adapter-lab.md` | 2026-05-21 |

---

## Planned (no task folder yet)

| Version | PRD | Blocked by |
|---------|-----|------------|
| v2.5.5 | [prd-v2.5.5-formal-spec-interop.md](../../product/prd/prd-v2.5.5-formal-spec-interop.md) | v2.5.4 soft (docs-only may overlap); hard: v2.5.0 + identity model |
| v3.0 | [prd-v3.0-economy.md](../../product/prd/prd-v3.0-economy.md) | Launch triggers (not Dist ship) |

Create `engineering/tasks/v{X}/` when each adoption release starts. Dist handoff checklist: [v2.5.4/release-checklist.md §5.1](./v2.5.4/release-checklist.md#51-handoff-inputs-for-v255-confirm-at-s5).

---

## v2.2.0 parallel tracks (same release)

- [tasks-v2.2.0-protocol-hardening.md](./v2.2.0/tasks-v2.2.0-protocol-hardening.md)
- [design-system-revamp/](./v2.2.0/design-system-revamp/)
- [tasks-a2h-integration.md](./v2.2.0/tasks-a2h-integration.md)
- [tasks-cross-platform-integration-asap.md](./v2.2.0/tasks-cross-platform-integration-asap.md)

---

## Naming convention

```
engineering/tasks/v{major}.{minor}.{patch}/tasks-v{X}-roadmap.md
engineering/tasks/v{X}/sprint-S{N}-{slug}.md
engineering/tasks/v{X}/release-checklist.md
```

Private execution: `engineering/tasks/private/v{X}/` mirrors the same pattern.
