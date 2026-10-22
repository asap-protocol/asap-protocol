# Rate Limiting Testing Patterns - Lessons Learned

> **Document**: Rate Limiting Testing Best Practices
> **Sprint Context**: v0.5.0 S2.5 (Issue #17) and v1.0.0 P5-P6
> **Last Updated**: 2026-01-31
>
> **Enforceable rule and quick checklist**: `.cursor/rules/testing-rate-limiting.mdc`

---

## Executive Summary

**Problem**: `slowapi.Limiter` maintains global state that persists across test executions, causing rate limit interference (HTTP 429) in test suites.

**Solution**: "Aggressive Monkeypatch" pattern + isolated storage URIs + process isolation with pytest-xdist.

**Impact**: Resolved 33 failing tests in v0.5.0, prevented similar issues in v1.0.0.

---

## Root Cause Analysis

### The Problem

The `slowapi` library uses a **singleton-like pattern** where the `Limiter` instance maintains shared state:

1. **Module-level decorator**: `@limiter.limit()` captures the limiter at import time
2. **Global state accumulation**: Request counts persist across test runs
3. **TestClient limitations**: Even with `TestClient(app)`, the decorator still references the original module-level limiter
4. **UUID storage isn't enough**: Creating limiters with unique `storage_uri=f"memory://{uuid.uuid4()}"` only isolates the storage, not the limiter reference itself

### Why Standard Approaches Fail

```python
# ❌ INSUFFICIENT: Only replacing app.state.limiter
app = create_app(manifest, rate_limit="100000/minute")
app.state.limiter = isolated_limiter  # Decorator still uses old limiter!
client = TestClient(app)
# Still gets HTTP 429 from previous tests

# ❌ INSUFFICIENT: Unique storage alone
limiter1 = Limiter(storage_uri=f"memory://{uuid.uuid4()}")
limiter2 = Limiter(storage_uri=f"memory://{uuid.uuid4()}")
# Different storage, but decorator captured limiter1 at import time
```

---

## The Solution: Aggressive Monkeypatch Pattern

### Strategy Overview

1. **Process Isolation** (pytest-xdist): Run tests in separate processes
2. **Aggressive Monkeypatch**: Replace limiter in BOTH middleware and server modules
3. **Isolated Storage**: Each limiter gets unique UUID-based storage
4. **Strategic Organization**: Separate rate-limiting tests from other tests

### Implementation Pattern

Apply the **canonical pattern** (steps, critical rules, and code block) from `.cursor/rules/testing-rate-limiting.mdc`. Summary: create an isolated limiter with `storage_uri=f"memory://{uuid.uuid4()}"`, monkeypatch `limiter` in both `asap.transport.middleware` and `asap.transport.server` *before* creating the app, then create the app and set `app.state.limiter`. Use high limits (e.g. `"999999/minute"`) for tests that are not asserting rate limiting; use moderate limits (e.g. `"10/minute"`) only in dedicated rate-limiting tests.

#### Pattern 2: Using Test Fixtures (v0.5.0 Approach)

```python
# In tests/transport/conftest.py
@pytest.fixture
def isolated_limiter_factory() -> Callable[[Sequence[str] | None], "Limiter"]:
    """Factory fixture that creates isolated rate limiters."""
    def _create(limits: Sequence[str] | None = None) -> "Limiter":
        if limits is None:
            limits = ["100000/minute"]
        unique_storage_id = str(uuid.uuid4())
        return Limiter(
            key_func=get_remote_address,
            default_limits=list(limits),
            storage_uri=f"memory://{unique_storage_id}",
        )
    return _create

@pytest.fixture
def replace_global_limiter(
    monkeypatch: pytest.MonkeyPatch,
    isolated_limiter_factory: Callable[[Sequence[str] | None], "Limiter"],
) -> "Limiter":
    """Replace global limiter with isolated instance using aggressive monkeypatch."""
    new_limiter = isolated_limiter_factory(None)
    
    import asap.transport.middleware as middleware_module
    import asap.transport.server as server_module
    monkeypatch.setattr(middleware_module, "limiter", new_limiter)
    monkeypatch.setattr(server_module, "limiter", new_limiter)
    
    return new_limiter

# In tests
def test_with_fixtures(sample_manifest: Manifest, replace_global_limiter: Limiter) -> None:
    """Test using the fixture - limiter already replaced."""
    app = create_app(sample_manifest, rate_limit="100000/minute")
    app.state.limiter = replace_global_limiter
    # Test code here
```

#### Pattern 3: NoRateLimitTestBase Mixin (v0.5.0)

```python
# In tests/transport/conftest.py
class NoRateLimitTestBase:
    """Base class for tests that should not use rate limiting."""
    
    @pytest.fixture(autouse=True)
    def disable_rate_limiting(self, monkeypatch: pytest.MonkeyPatch) -> "Limiter":
        """Automatically disable rate limiting for all tests in this class."""
        from slowapi import Limiter
        from slowapi.util import get_remote_address
        
        # Create limiter with NO limits
        no_limit_limiter = Limiter(
            key_func=get_remote_address,
            storage_uri=f"memory://no-limits-{uuid.uuid4().hex}",
            default_limits=[],  # Empty list = no limits
        )
        
        import asap.transport.middleware as middleware_module
        import asap.transport.server as server_module
        monkeypatch.setattr(middleware_module, "limiter", no_limit_limiter)
        monkeypatch.setattr(server_module, "limiter", no_limit_limiter)
        
        return no_limit_limiter

# Usage
class TestMyFeature(NoRateLimitTestBase):
    """Tests for my feature without rate limiting interference."""
    
    def test_something(self):
        # Rate limiting is automatically disabled
        pass
```

---

## Process Isolation with pytest-xdist

### Setup

```toml
# pyproject.toml
[project.optional-dependencies]
test = [
    "pytest-xdist>=3.5.0",
    # ... other test dependencies
]
```

### Usage

```bash
# Run tests in parallel (auto-detect CPU count)
pytest -n auto

# Run with specific number of workers
pytest -n 4

# Disable parallel execution (useful for debugging)
pytest -n 0
```

### CI Configuration

```yaml
# .github/workflows/ci.yml (coverage is collected in the parallel test job)
- name: Run tests with coverage
  run: uv run pytest -n auto --tb=short --cov=asap --cov-report=xml
```

---

## Test Organization Strategy

### Directory Structure (v0.5.0)

```
tests/
├── transport/
│   ├── conftest.py           # Isolation fixtures
│   ├── unit/                 # Pure logic tests
│   │   └── test_bounded_executor.py
│   ├── integration/          # HTTP/Server tests
│   │   ├── test_rate_limiting.py      # Rate limiting ONLY
│   │   ├── test_server_core.py        # Core without rate limiting
│   │   └── test_request_size_limits.py
│   └── e2e/                  # End-to-end scenarios
│       └── test_full_agent_flow.py
```

### Rationale

1. **Separate rate limiting tests**: Isolate tests that specifically test rate limiting
2. **Disable rate limiting elsewhere**: Use `NoRateLimitTestBase` or high limits
3. **Process isolation**: pytest-xdist ensures each worker has fresh state

---

## Decision Tree: When to Use Each Pattern

Canonical version: `.cursor/rules/testing-rate-limiting.mdc`

```
┌─ Testing rate limiting functionality itself?
│  ├─ YES → Use Pattern 1 (Aggressive Monkeypatch) + moderate limits (10/minute)
│  │        Test in dedicated test_rate_limiting.py file
│  └─ NO  → Continue ↓
│
└─ Multiple tests in same class need isolation?
   ├─ YES → Use Pattern 3 (NoRateLimitTestBase mixin)
   │        All tests inherit automatic isolation
   └─ NO  → Use Pattern 1 (Aggressive Monkeypatch) + high limits (999999/minute)
            Single test needs isolation
```

---

## Common Pitfalls and Solutions

For **critical rules** and **common mistakes** (only replacing `app.state.limiter`, patching only one module, creating app before monkeypatching), see `.cursor/rules/testing-rate-limiting.mdc`.

### Pitfall: Reusing TestClient Across Tests

```python
# ❌ WRONG (shared client)
@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)  # Accumulates state across tests

# ✅ CORRECT (per-test client)
@pytest.fixture(scope="function")
def client(app):
    return TestClient(app)  # Fresh for each test
```

---

## Real-World Examples from Codebase

### Example 1: v1.0.0 P5-P6 Debug Log Test

**Context**: Test for ASAP_DEBUG_LOG mode was failing with HTTP 429 because previous tests exhausted rate limit.

**Problem**:
```python
def test_debug_log_mode_logs_request_and_response(
    self, client: TestClient, sample_manifest: Manifest
) -> None:
    # Uses shared client fixture - rate limit already exceeded!
    response = client.post("/asap", json=body)
    assert response.status_code == 200  # ❌ FAILS: HTTP 429
```

**Solution**:
```python
def test_debug_log_mode_logs_request_and_response(
    self, sample_manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uuid
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    # Aggressive monkeypatch
    isolated_limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["999999/minute"],
        storage_uri=f"memory://{uuid.uuid4()}",
    )
    
    import asap.transport.middleware as middleware_module
    import asap.transport.server as server_module
    monkeypatch.setattr(middleware_module, "limiter", isolated_limiter)
    monkeypatch.setattr(server_module, "limiter", isolated_limiter)

    # Create fresh app with isolated limiter
    app = create_app(sample_manifest, rate_limit="999999/minute")
    app.state.limiter = isolated_limiter
    client = TestClient(app)
    
    response = client.post("/asap", json=body)
    assert response.status_code == 200  # ✅ PASSES
```

### Example 2: v0.5.0 Issue #17 Resolution

**Context**: 33 tests failing due to rate limit interference in full test suite.

**Investigation**:
- Tests passed individually: `pytest tests/transport/test_server.py::TestFoo::test_bar` ✅
- Tests failed in suite: `pytest tests/transport/` ❌
- Root cause: Accumulated rate limit state from previous tests

**Solution Applied**:
1. Added pytest-xdist for process isolation
2. Created `tests/transport/conftest.py` with isolation fixtures
3. Reorganized tests into `unit/`, `integration/`, `e2e/`
4. Applied aggressive monkeypatch pattern
5. Created `NoRateLimitTestBase` mixin

**Result**: 578/578 tests passing, 0 failures, 89.42% coverage

---

## Production Code Patterns

### Creating Isolated Limiters in Production

```python
# src/asap/transport/middleware.py
def create_limiter(limits: Sequence[str] | None = None) -> Limiter:
    """Create a new limiter instance for production use.
    
    Each limiter gets its own storage to ensure isolation between
    multiple FastAPI app instances.
    """
    if limits is None:
        limits = [DEFAULT_RATE_LIMIT]
    
    # Use unique storage URI to ensure isolation
    unique_storage_id = str(uuid.uuid4())
    return Limiter(
        key_func=_get_sender_from_envelope,
        default_limits=list(limits),
        storage_uri=f"memory://{unique_storage_id}",
    )

# src/asap/transport/server.py
def create_app(manifest: Manifest, rate_limit: str | None = None) -> FastAPI:
    """Create FastAPI app with isolated rate limiter."""
    # ...
    app.state.limiter = create_limiter([rate_limit_str])
    # Each app instance gets its own limiter with isolated storage
```

---

## Checklist for New Tests Involving Rate Limiting

Use the **decision tree** and **critical rules** in `.cursor/rules/testing-rate-limiting.mdc` as the canonical checklist. Summary:

- [ ] Chose pattern from decision tree (rate-limiting test? → moderate limits + dedicated file; else → high limits or `NoRateLimitTestBase`).
- [ ] Applied aggressive monkeypatch per the rule (both modules, before creating app, unique storage, `app.state.limiter`).
- [ ] Test organization: rate-limiting tests in dedicated file; process isolation (`pytest -n auto`); fixture scope `function`, not `module`.

---

## Audit: Codebase Compliance (2026-02-04)

Applied `.cursor/rules/testing-rate-limiting.mdc` and verified rate limit handling:

| Area | Status | Notes |
|------|--------|--------|
| **tests/conftest.py** | ✅ | `_isolate_rate_limiter` (autouse) patches both modules; limit aligned to `999999/minute`. |
| **tests/transport/conftest.py** | ✅ | `isolated_limiter_factory`, `replace_global_limiter`, `NoRateLimitTestBase`, `create_isolated_app` follow the rule. |
| **tests/transport/integration/test_rate_limiting.py** | ✅ | Aggressive monkeypatch + moderate limits; dedicated file. |
| **tests/transport/integration/test_server_core.py** | ✅ | All test classes use `NoRateLimitTestBase`. |
| **tests/transport/test_server.py** | ✅ | `app`/`client` fixtures rely on global isolation; `TestDebugLogMode` uses full pattern + `app.state.limiter`. |
| **tests/transport/e2e/test_full_agent_flow.py** | ✅ | `NoRateLimitTestBase` + high-limit app. |
| **tests/contract/conftest.py** | ✅ | `disable_rate_limiting_for_contract_tests` patches both modules (no-limit). |
| **tests/test_docs_troubleshooting_smoke.py** | ✅ | Uses `default_app`; decorator uses module-level limiter patched by autouse fixture. |
| **benchmarks/conftest.py** | ✅ Fixed | Switched from `limiter.enabled = False` to aggressive monkeypatch + isolated limiter. |

**Optional improvement**: Fixtures that create an app (e.g. `test_server.app`) could set `app.state.limiter` to the same limiter used in the patch for full runtime consistency; the module-level patch is sufficient to avoid HTTP 429.

---

## References

### Sprint Context
- **v0.5.0 Sprint S2**: Added slowapi, implemented rate limiting
- **v0.5.0 Sprint S2.5**: Resolved Issue #17 (33 failing tests)
- **v1.0.0 Sprint P5-P6**: Applied lessons learned, prevented regressions

### Related Documents
- `.cursor/rules/testing-rate-limiting.mdc` - Enforceable rule and quick reference (canonical pattern and critical rules)
- `engineering/tasks/v0.5.0/tasks-v0.5.0-s2-detailed.md` - Initial rate limiting implementation
- `engineering/tasks/v0.5.0/tasks-v0.5.0-s2.5-detailed.md` - Problem resolution
- `engineering/tasks/v1.0.0/upstream-slowapi-deprecation.md` - slowapi deprecation warning

### Test Fixtures
- `tests/transport/conftest.py` - Isolation fixtures (isolated_limiter_factory, replace_global_limiter, NoRateLimitTestBase)
- `tests/conftest.py` - Global fixtures

### Production Code
- `src/asap/transport/middleware.py` - create_limiter(), create_test_limiter()
- `src/asap/transport/server.py` - create_app() with limiter setup

---

## Upstream Issue

**slowapi Deprecation Warning** (Python 3.14+):
```
DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16
```

**Status**: Tracked in slowapi PR #246 (open)
**Workaround**: pytest filter in `pyproject.toml`
**Action**: Bump dependency when fix is released

See: `engineering/tasks/v1.0.0/upstream-slowapi-deprecation.md`

---

**Last Updated**: 2026-01-31 by Sprint P5-P6
**Next Review**: When adding new rate limiting tests or experiencing test interference
