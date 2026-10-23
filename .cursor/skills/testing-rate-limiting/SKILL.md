---
name: testing-rate-limiting
description: Apply deterministic SlowAPI rate-limiter isolation for FastAPI transport tests, especially to avoid flaky HTTP 429 responses.
disable-model-invocation: false
---

# Testing Rate Limiting (SlowAPI)

## Pre-requisites

- `.cursor/rules/testing-rate-limiting.mdc` (bridge rule)
- `.cursor/rules/testing-standards.mdc` (pytest + xdist; coverage stays out of addopts; `serial_mcp` stays out of `-n auto`)
- `.cursor/README.md` (canonical test commands)

## When to use

- Writing or reviewing tests that hit rate-limited transport endpoints (e.g., `/asap`).
- Fixing flaky tests caused by shared SlowAPI limiter state or unexpected HTTP 429 responses.

## Required isolation sequence

1. Create an isolated `Limiter` with a unique `memory://{uuid.uuid4()}` storage before app creation.
2. Monkeypatch `limiter` in both modules before creating the app:
   - `asap.transport.middleware`
   - `asap.transport.server`
3. Create the app only after both monkeypatch operations.
4. Set `app.state.limiter` after app creation for runtime consistency.

## Reference pattern

```python
def test_something(
    sample_manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid

    from slowapi import Limiter
    from slowapi.util import get_remote_address

    isolated_limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["999999/minute"],
        storage_uri=f"memory://{uuid.uuid4()}",
    )

    import asap.transport.middleware as middleware_module
    import asap.transport.server as server_module

    monkeypatch.setattr(middleware_module, "limiter", isolated_limiter)
    monkeypatch.setattr(server_module, "limiter", isolated_limiter)

    app = create_app(sample_manifest, rate_limit="999999/minute")
    app.state.limiter = isolated_limiter
    client = TestClient(app)
```

## Mandatory anti-patterns

- Creating the app before monkeypatching `limiter`.
- Monkeypatching only `app.state.limiter`.
- Monkeypatching only one of the transport modules.

## Optional alternatives

- For tests that do not require rate-limit enforcement, prefer `NoRateLimitTestBase` / isolated fixtures from `tests/transport/conftest.py`.
- For full historical context, read:
  - `engineering/architecture/rate-limiting.md`
  - `tests/transport/conftest.py`
  - `tests/conftest.py`
