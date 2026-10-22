# Transport module evolution (D4 baseline)

Product constraint **D4** requires every **new transport route or callable surface** to live in a **dedicated module**, not by extending the transport server or client monoliths indefinitely.

CI enforces a frozen baseline of **public function and method names** on the transport server module and the transport client package.

## What is measured

- **`src/asap/transport/server.py`** — single file; its public callables are measured directly.
- **`src/asap/transport/client/`** — **package** (decomposed from the original `client.py` monolith during the v2.5.1 transport decomposition, Sprint S2). The linter aggregates public symbols across **every** `*.py` module in the package directory. Methods are named by their **defining class** (e.g. `ASAPClient.batch` when defined on `ASAPClient`, `_SendMixin.send` when defined on a mixin) — this tracks the surface at its definition site without resolving inheritance, so a method moved into a mixin during decomposition is still guarded.

## Enforcement

- **Script**: [`scripts/lint_no_transport_growth.py`](../../scripts/lint_no_transport_growth.py) — parses AST only (no imports from ASAP).
- **Baseline snapshot**: [`scripts/_transport_baseline_v2.5.1.json`](../../scripts/_transport_baseline_v2.5.1.json) — frozen at **v2.5.1** (supersedes the v2.3.0 baseline, which measured the pre-decomposition `client.py` file).
- **Workflow**: GitHub Actions job **`lint transport growth (D4)`** in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
- **Tests**: [`tests/scripts/test_lint_no_transport_growth.py`](../../tests/scripts/test_lint_no_transport_growth.py) covers the package-aggregation path.

Adding any **new public symbol** (names not starting with `_`; excludes nested defs inside functions) to the server module or to any module in the client package **breaks CI**. Removing symbols is allowed.

Protect main does not require `lint transport growth (D4)`. The job still runs for Python changes.

## Escape hatch (rare)

Updating the baseline is intentional governance overhead — only after architectural agreement.

1. **Architectural review** — justify why new surface cannot ship from a new submodule under `src/asap/transport/` (or another boundary consistent with ADRs).
2. **Maintainer sign-off** — explicit approval on the PR (link reviewer handles).
3. **Same PR** — regenerate or edit **`scripts/_transport_baseline_v2.5.1.json`** together with the code change:

   ```bash
   uv run python scripts/lint_no_transport_growth.py --emit-baseline > scripts/_transport_baseline_v2.5.1.json
   ```

   Inspect the diff; commit only intentional additions.

The v2.5.1 baseline re-freeze used this escape hatch: the S2 decomposition split `client.py` into the `client/` package and moved `ASAPRequestHandler` out of `server.py`, so the baseline was regenerated against the new package-aggregated surface (methods attributed to their defining classes, including the `_SendMixin`/`_DiscoveryMixin` that now carry the client's public methods).

Do **not** widen the baseline to silence unrelated churn — refactor helpers into dedicated modules instead.
