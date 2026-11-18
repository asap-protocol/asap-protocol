# PRD: ASAP Economy — Product Track (formerly v3.0.0)

> **Product Requirements Document**
>
> **Version**: product track — **not** a library version. Library major `v3.0.0` is reserved for breaking protocol or wire changes ([release policy](../../docs/maintainers/release-policy.md)).
> **Status**: VISION DRAFT (trigger-gated; triggers in §1.1 unchanged)
> **Created**: 2026-02-25
> **Last Updated**: 2026-11-18
> **Train index**: [prd-roadmap.md](./prd-roadmap.md) — Economy row
> **Predecessor (library)**: [prd-spec-profile.md](./prd-spec-profile.md) (v2.7.0, after [prd-interop.md](./prd-interop.md) v2.6.0); decision gate in [prd-roadmap.md §2](./prd-roadmap.md#2-roadmap)

> [!NOTE]
> **Reframed 2026-11-18.** Economy is a **product / services track**, decoupled from library semver. The filename and older text still say "v3.0" (and "v4.0+" for later phases). Read "v3.0" as "Economy track launch" and "v4.0+" as "a later Economy phase", not as `asap-protocol` versions. If the track needs library changes (for example the ECO-002 manifest field), they ship under the release policy: additive changes in a library minor, breaking changes only in a library major.

---

## 1. Executive Summary

### 1.1 Purpose

The Economy track transforms ASAP from an **Agent Directory** into an **Agent Economy**. The track activates the economy layer that has been in the vision since day one but deliberately deferred until the network effect is proven:

- **Payment Processing**: Stripe integration for Verified Badge subscriptions ($49/mo)
- **Economy Settlement**: Pay-per-use billing, credits, agent-to-agent payments
- **Clearing House**: Protocol-level transaction fees and marketplace revenue
- **ASAP Cloud (Alpha)**: Managed agent hosting ("Vercel for Agents")

> [!CAUTION]
> This PRD is a **VISION DRAFT** — do not start implementation until the following triggers are met:
> - Verified Badge revenue potential: 100+ Verified Agents who would pay $49/mo
> - OR: Agent-to-agent transaction volume exceeds $5k/month equivalent
> - AND: Legal/tax infrastructure is ready (Stripe Tax, Nexus, VAT registration)

### 1.2 Strategic Context

```
v1.x (protocol) → v2.x (marketplace) → v3.0 (economy)
Open & Free        Network effect        Monetization
```

> Since 2026-11-18 the third step is a **product track**, not library `v3.0.0`. The library keeps its own semver line ([prd-roadmap.md](./prd-roadmap.md)).

The Open Core model (ADR per `vision-agent-marketplace.md §5`) means the SDK and Lite Registry remain free forever. v3.0 monetizes the **services layer**: the Clearing House, Verified Badge, and ASAP Cloud.

### 1.3 Revenue Targets (Launch Criteria)

| Signal | Threshold | Status |
|--------|----------|--------|
| Verified Badge candidates | 100+ agents who meet quality bar | TBD |
| Monthly active consumers (SDK) | 10,000+ URN resolutions/month | TBD |
| Agent-to-agent automaton volume | Measurable transaction flow | TBD |

---

## 2. Goals

| Goal | Metric | Priority |
|------|--------|----------|
| Revenue activation | $10k MRR within 6 months of launch | P1 |
| Verified Badge monetization | 100 paying customers @ $49/mo | P1 |
| Pay-per-use Economy | 10 agents offering paid services via ASAP | P1 |
| ASAP Cloud alpha | 10 agents hosted on ASAP Cloud | P2 |
| Dispute rate | < 1% of transactions disputed | P1 |

---

## 3. User Stories

### Verified Agent Developer
> As a **Verified Agent developer**, I want to **pay $49/month via Stripe** so that **my agent displays the Verified badge and appears higher in search results**.

### Pay-per-Use Agent Provider
> As an **agent provider**, I want to **configure a price per task in my manifest** so that **consumers are automatically billed when they use my agent via the ASAP SDK**.

### Agent Consumer (Pay-per-Use)
> As a **consumer developer**, I want to **pre-fund ASAP credits** so that **my orchestration loop can call paid agents without manual billing per call**.

### ASAP Cloud Developer
> As a **developer**, I want to **deploy my ASAP-compliant agent with `asap deploy`** so that **I don't need to manage my own infrastructure**.

---

## 4. Functional Requirements

### 4.1 Payment Processing — Verified Badge Subscriptions (P1)

| ID | Requirement | Priority |
|----|-------------|----------|
| PAY-001 | Stripe Checkout integration for the Verified Badge ($49/month) | MUST |
| PAY-002 | Webhook handler: activate/deactivate badge on payment success/failure | MUST |
| PAY-003 | Stripe Customer Portal link in developer dashboard (manage subscription) | MUST |
| PAY-004 | Tax handling: Stripe Tax for VAT/GST (EU, AU, CA compliance) | MUST |
| PAY-005 | Receipt emails via Stripe (no custom email infra needed) | MUST |
| PAY-006 | Grace period: 7-day lapse before badge is removed on payment failure | SHOULD |

---

### 4.2 Economy Settlement — Credits & Pay-per-Use (P1)

| ID | Requirement | Priority |
|----|-------------|----------|
| ECO-001 | Credit system: developers pre-fund ASAP Credits (1 Credit = $0.01) | MUST |
| ECO-002 | Manifest pricing field: `price_per_task_credits: int` in `SLASection` | MUST |
| ECO-003 | SDK auto-deducts credits on `agent.run()` with sufficient credit check before call | MUST |
| ECO-004 | Insufficient credits: `InsufficientCreditsError` raised before handshake | MUST |
| ECO-005 | Credit top-up: Stripe Payment Element in developer dashboard | MUST |
| ECO-006 | Credit ledger: per-consumer, per-agent transaction log | MUST |
| ECO-007 | Agent payout: monthly credits-to-cash settlement for providers (Stripe Connect) | SHOULD |
| ECO-008 | Clearing House fee: ASAP retains X% of each transaction (TBD, est. 5–10%) | SHOULD |

**Credit flow**:
```
Consumer pre-funds 1000 credits ($10)
  → Consumer calls MathAgent (2 credits/task)
  → SDK: credit_balance check before handshake
  → SDK: deducts 2 credits on success
  → ASAP retains 5% (0.1 credit) as Clearing House fee
  → Provider receives 1.9 credits
  → Month-end: providers receive Stripe Connect payout
```

---

### 4.3 Escrow & Dispute Resolution (P2)

| ID | Requirement | Priority |
|----|-------------|----------|
| ESC-001 | Escrow hold: credits locked at task start, released on completion | SHOULD |
| ESC-002 | Dispute: consumer can flag within 24h (evidence: task logs + audit trail) | SHOULD |
| ESC-003 | Auto-resolution: if agent has > 99% success rate and task logs show completion, auto-resolve | COULD |
| ESC-004 | Manual arbitration queue for ASAP team on contested disputes | SHOULD |

---

### 4.4 SLA Financial Compensation (P2)

> Extensions to the SLA Framework from v1.3, adding financial penalties.

| ID | Requirement | Priority |
|----|-------------|----------|
| SLA-FIN-001 | `compensation` block in manifest: `missed_response`, `missed_completion`, `outage` | SHOULD |
| SLA-FIN-002 | SDK auto-triggers compensation if SLA breach is detected | COULD |
| SLA-FIN-003 | Credits refund to consumer on confirmed SLA breach | SHOULD |

---

### 4.5 ASAP Cloud (Alpha) (P2)

> "Vercel for Agents" — managed agent hosting.

| ID | Requirement | Priority |
|----|-------------|----------|
| CLOUD-001 | `asap deploy` CLI command: packages and deploys ASAP-compliant agents to managed infra | SHOULD |
| CLOUD-002 | Auto-assigns HTTPS endpoint and registers in marketplace | SHOULD |
| CLOUD-003 | Pricing: per-compute-minute billing via credits or subscription | SHOULD |
| CLOUD-004 | Monitoring: built-in health checks, SLA tracking, error logging | SHOULD |

---

## 5. Non-Goals (Out of Scope)

| Feature | Reason | When |
|---------|--------|------|
| Crypto/DeFi settlement | Pluggable architecture keeps door open; separate repo when triggers met | v4.0+ (separate repo) |
| Federated registry | Centralized approach still validates at v3 | v3.x+ |
| Enterprise SSO / SAML | After enterprise traction in v2.x | TBD |
| Real-time bidding / auctions | Complex, low priority at this stage | v4.0+ |

> [!NOTE]
> **Crypto settlement is NOT cancelled** — it is architecturally planned via the `SettlementBackend` Protocol interface (see §6.4). The v3.0 economy ships with Stripe as the reference backend. DeFi settlement (stablecoins on L2) will be built in a separate repository (`asap-settlement-crypto`) when transaction volume and user demand justify it. See [§6.2 Pluggable Settlement Architecture](#62-pluggable-settlement-architecture) for the full analysis.

---

## 6. Technical Considerations

### 6.1 Payment Infrastructure

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Payment processor | Stripe (Checkout, Connect, Tax) | Industry standard, marketplace payouts, tax automation |
| Webhooks | Stripe Webhooks → Vercel Edge Functions | Serverless, no additional backend |
| Credit ledger | PostgreSQL (from v2.3 Registry API Backend) | Atomic transactions, ACID |
| Payout | Stripe Connect (Express accounts) | Agent providers onboard without explicit banking relationship |

### 6.2 Pluggable Settlement Architecture

All economy features MUST use the `SettlementBackend` Protocol interface — NOT direct Stripe calls. This enables future settlement backends (crypto/DeFi) without changing the economy logic.

```python
@runtime_checkable
class SettlementBackend(Protocol):
    async def hold(self, payer_id: str, amount_credits: int, task_id: str) -> HoldReceipt: ...
    async def release(self, receipt: HoldReceipt, payee_id: str, fee_pct: float) -> Settlement: ...
    async def refund(self, receipt: HoldReceipt) -> Settlement: ...
    async def balance(self, account_id: str) -> int: ...
    async def top_up(self, account_id: str, amount_credits: int) -> str: ...
```

v3.0 ships `StripeSettlement` as the reference implementation. Future `CryptoSettlement` (stablecoins on L2) will be in a separate repo implementing the same Protocol.

**Reference**: [§6.2 Pluggable Settlement Architecture](#62-pluggable-settlement-architecture)

### 6.3 Economic Safety

- **Pre-funding only**: No billing after the fact — reduces fraud and chargebacks
- **Hard limits**: Consumers can set max daily spend
- **Fraud detection**: Flag accounts with unusual consumption spikes before dispute

### 6.4 Legal Prerequisites

Before launching economy features:
- [ ] Terms of Service updated with economic provisions (escrow, dispute, refund policy)
- [ ] Privacy Policy updated for Stripe data sharing
- [ ] Business registration sufficient for Stripe merchant account
- [ ] Nexus/VAT evaluation for each target market

---

## 7. Success Metrics

| Metric | Target (6 months post-launch) |
|--------|-------------------------------|
| MRR (Verified Badges) | $4,900 (100 badges × $49) |
| Credit GMV | $10,000/month (gross marketplace volume) |
| ASAP Revenue (10% fee) | $1,000/month from Clearing House |
| ASAP Cloud agents | 10 deployed |
| Dispute rate | < 1% |

---

## 8. Prerequisites from v2.x

| Prerequisite | Source | Notes (2026-07-18) |
|-------------|--------|---------------------|
| ASAP OAuth2 infrastructure | v1.1+ | Shipped |
| Per-runtime-agent identity & capabilities | v2.2 | Shipped |
| Audit Logging | v2.2 | Shipped |
| Usage Metering (`MeteringStore`) | v1.3+ | Shipped |
| TypeScript SDK | v2.3 | Shipped (`@asap-protocol/client`) |
| OpenAPI adapter (provider onboarding) | **v2.3.0** (not v2.4) | Shipped; Dist thin starter under `examples/starters/openapi-provider/` |
| Consumer / agent-first distribution | **v2.5.4** Distribution Loop | Soft: narrative + starters + metric proxies — [PRD](./prd-v2.5.4-distribution-loop.md) |
| Interop (A2A / MCP bridges, external tokens) | **v2.6.0** — [prd-interop.md](./prd-interop.md) | Soft; planned. Not a hard Economy gate (2026-11-18) |
| Spec profile | **v2.7.0** — [prd-spec-profile.md](./prd-spec-profile.md) (was "Formal Spec v2.5.5") | Soft; standards-track; not a hard Economy gate |
| Registry API Backend (PostgreSQL) | Deferred | **Not shipped.** Still gated by 500-agent / IssueOps trigger ([prd-roadmap.md §5](./prd-roadmap.md#5-parked-and-deferred-items)). Credit ledger in §6.1 must not assume it exists yet. |
| 100+ Verified agent candidates | Growth trigger | Candidate **proxies** from Dist DIST-004: registry count, npm/PyPI installs, site→docs CTR — not a substitute for the paid-badge trigger itself |
| Legal/tax infrastructure | §6.4 | Required with revenue triggers |

### 8.1 ADR-21 vs paid Verified Badge

Until Economy launch triggers fire, public trust remains **merit-based / free** per ADR-21 ([05-product-strategy.md](../decision-records/05-product-strategy.md)). The **$49/mo** badge in this PRD is a **future** product change; do not preview pricing in public copy for any library release (Dist DIST-006).

---

## 9. Open Questions

- **Q1**: What percentage does ASAP retain per transaction? (Start: 5–10%, validate with providers)
- **Q2**: Are credits tied to USD or is there a pegged internal currency?
- **Q3**: Should ASAP Cloud be a separate product (separate repo, separate billing) or part of `asap-protocol`?
- **Q4**: ~~Crypto payments: deferred indefinitely or worth revisiting if L2 settlement costs drop to < $0.001?~~ **RESOLVED (2026-03-13)**: Crypto settlement is planned as v4.0+ extension in separate repo (`asap-settlement-crypto`). v3.0 designs `SettlementBackend` Protocol interface to keep door open. See [§6.2 Pluggable Settlement Architecture](#62-pluggable-settlement-architecture).
- **Q5** (added 2026-11-18): Before building a separate settlement layer or Clearing House (§4.2, §6.2), evaluate interop with the **A2A AP2 payments extension**. Can a `SettlementBackend` sit behind AP2 instead of beside it? Confirm AP2's scope and status against its source spec at evaluation time; this PRD does not assert them.

---

## 10. Related Documents

- **Train index**: [prd-roadmap.md](./prd-roadmap.md)
- **Spec profile (v2.7.0)**: [prd-spec-profile.md](./prd-spec-profile.md) · **Interop (v2.6.0)**: [prd-interop.md](./prd-interop.md)
- **Closed v2.5.x train**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md)
- **Distribution Loop (adoption metrics / narrative)**: [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md)
- **Vision**: [prd-v2.0-roadmap.md](./prd-v2.0-roadmap.md) §3 (Economy Layer) and §5.3 (Pricing Strategy)
- **Deferred scope (original backlog sections)**: [05-product-strategy.md](../decision-records/05-product-strategy.md) (see Questions 10, 15, 21–22 for deferrals; ADR-21 Verified Badge free until Economy)
- **Legacy adoption redirect**: [prd-v2.4-adoption.md](./prd-v2.4-adoption.md)
- **Crypto settlement extension**: [§6.2 Pluggable Settlement Architecture](#62-pluggable-settlement-architecture)

---

## Change Log

| Date | Version | Change |
|------|---------|--------|
| 2026-11-18 | 0.4.0 | Reframed as a **product / services track**, decoupled from library semver (library `v3.0.0` reserved for breaking protocol changes). Status VISION DRAFT and all triggers unchanged. Q5: evaluate A2A AP2 payments interop before a separate settlement layer / Clearing House. Predecessor links → [prd-roadmap.md](./prd-roadmap.md), [prd-spec-profile.md](./prd-spec-profile.md), [prd-interop.md](./prd-interop.md). No new pricing, revenue, or GTM detail. |
| 2026-07-18 | 0.3.1 | Prerequisites corrected (OpenAPI = v2.3; Registry API deferred); Dist metrics as trigger proxies; ADR-21 vs paid badge note; cross-links to v2.5.4/v2.5.5 |
| 2026-02-25 | 0.1.0 | Vision DRAFT — consolidates deferred-backlog §4, §5 and vision-agent-marketplace §3 |
| 2026-03-13 | 0.2.0 | Added §6.2 Pluggable Settlement Architecture (SettlementBackend Protocol). Q4 resolved: crypto settlement planned as v4.0+ in separate repo. Non-Goals updated. |
| 2026-03-20 | 0.3.0 | Updated prerequisites: Registry API Backend is v2.3 (not v2.2). Added v2.2 identity/capabilities, v2.3 TypeScript SDK, v2.4 OpenAPI adapter as prerequisites. Updated previous version link to v2.4. |
