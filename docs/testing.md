# Testing Guide

This guide covers the testing infrastructure for the ASAP Protocol implementation.

## Running Tests

**Always use `uv run pytest` to run tests:**

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/transport/integration/test_rate_limiting.py -v

# Run specific test
uv run pytest tests/transport/integration/test_rate_limiting.py::TestRateLimiting::test_requests_within_limit_succeed -v
```

Fast loop, local floor, and the CI argv: [Canonical commands](../.cursor/README.md#canonical-commands).

> **Coverage and xdist:** Do not put `--cov` in pytest `addopts`; a partial run would be judged against the project-wide 85% floor. `serial_mcp` stays out of `-n auto`.

**Why `uv run pytest` instead of `pytest`?**

The project requires Python 3.13+, but your system may have a global `pytest` installed under an older Python version (e.g., Python 3.9). Using `uv run pytest` ensures:

1. ✅ Correct Python version (from `.venv`, not system Python)
2. ✅ Correct pytest version (from project dependencies)
3. ✅ All project dependencies are available
4. ✅ Consistent with CI environment

**Alternative:** Activate the virtual environment first:

```bash
source .venv/bin/activate
pytest tests/transport/integration/test_rate_limiting.py -v
deactivate
```

---

## Test Organization

Tests are organized into three categories based on their scope and dependencies:

### Unit Tests (`tests/transport/unit/`)

Unit tests validate isolated components without external dependencies. These tests:
- Test individual classes or functions in isolation
- Have no HTTP dependencies
- Have no rate limiting dependencies
- Run very fast
- Are deterministic and repeatable

**Example**: `test_bounded_executor.py` tests the `BoundedExecutor` class without any HTTP or rate limiting.

### Integration Tests (`tests/transport/integration/`)

Integration tests validate component interactions within the transport layer. These tests:
- Test interactions between multiple components (e.g., server, middleware, handlers)
- May use HTTP clients (`TestClient`)
- Are organized by feature area:
  - `test_rate_limiting.py` - Rate limiting functionality (isolated file)
  - `test_request_size_limits.py` - Request size validation
  - `test_thread_pool_bounds.py` - Thread pool exhaustion handling
  - `test_metrics_cardinality.py` - Metrics cardinality protection
  - `test_server_core.py` - Core server functionality (endpoints, handlers, metrics)

**IMPORTANT**: Integration tests use fixtures from `tests/transport/conftest.py`. See the [Fixtures](#pytest-fixtures-explained) section for details.

### E2E Tests (`tests/transport/e2e/`)

End-to-end tests validate the full agent flow using real agent implementations. These tests:
- Test complete workflows from request to response
- Use the full stack (server, handlers, state management)
- Validate cross-component behavior
- Are slower but provide high confidence

**Example**: `test_full_agent_flow.py` tests complete round-trip agent interactions.

**Linking unit and integration:** E2E tests can reuse the same app (e.g. `create_app(manifest)`) with `httpx.ASGITransport(app=app)` so that `ASAPClient` talks to the real server over HTTP. For example, `TestManifestDiscovery::test_asap_client_get_manifest_against_app_and_cache` asserts that `get_manifest()` fetches from `/.well-known/asap/manifest.json` and that the manifest cache is used on the second call, connecting the unit tests in `tests/transport/test_client.py` (cache hit/miss) with the real endpoint.

**Future integration opportunities:**
- **MCP:** An integration test that runs the MCP server (e.g. in-process `run_stdio` or subprocess) and the MCP client, then calls `list_tools` and `call_tool`, would bridge the unit tests in `tests/mcp/` with a full round-trip.
- **Snapshot store:** If handlers or workflows start using `InMemorySnapshotStore`, an integration test that saves and deletes snapshots and asserts store state would align with the snapshot unit tests (e.g. `test_delete_last_version_cleans_up_task`).
- **Trace parser and logs:** A test that triggers a request, captures structured log output, and runs `parse_trace_from_lines()` (or `extract_trace_ids`) on it would ensure the observability log format stays compatible with the trace parser.

### Chaos Engineering Tests (`tests/chaos/`)

Chaos engineering tests validate system resilience under adverse conditions. These tests simulate real-world failure scenarios that distributed systems may encounter.

**Test modules:**

| Module | Tests | Description |
|--------|-------|-------------|
| `test_network_partition.py` | 12 | Network partition and connectivity issues |
| `test_crashes.py` | 13 | Server crashes and 5xx error responses |
| `test_message_reliability.py` | 19 | Message loss, duplication, and corruption |
| `test_clock_skew.py` | 25 | Clock synchronization and timestamp validation |

**Running chaos tests:**

```bash
# Run all chaos tests
uv run pytest tests/chaos/ -v

# Run specific chaos test module
uv run pytest tests/chaos/test_network_partition.py -v

# Run with verbose output for debugging
uv run pytest tests/chaos/ -v --tb=long
```

**Test categories:**

#### Network Partition (`test_network_partition.py`)

Simulates network-level failures:
- Complete connection failures (server unreachable)
- Connection timeouts (server unresponsive)
- Intermittent failures (flaky networks)
- DNS resolution failures
- SSL/TLS handshake failures
- Circuit breaker behavior under sustained failures

#### Server Crashes (`test_crashes.py`)

Simulates server-side failures:
- Server crash during request (connection reset)
- HTTP 502 Bad Gateway (proxy/load balancer issues)
- HTTP 503 Service Unavailable (server restarting)
- HTTP 504 Gateway Timeout (slow backend)
- Rolling restart scenarios (K8s deployments)
- OOM kill patterns
- Cascading failures

#### Message Reliability (`test_message_reliability.py`)

Simulates message-level issues:
- Message loss (timeout, no response)
- Message duplication (idempotency testing)
- Out-of-order delivery
- Partial message corruption (truncated JSON)
- Malformed responses
- Retry behavior with same envelope ID

#### Clock Skew (`test_clock_skew.py`)

Simulates clock synchronization issues:
- Past timestamps (stale messages, replay attacks)
- Future timestamps (sender clock ahead)
- Timezone handling (UTC, positive/negative offsets)
- Clock drift detection
- Multi-datacenter clock variance
- NTP sync failure scenarios
- VM snapshot/resume clock skew

**Key patterns used:**

1. **Mock transports**: Uses `httpx.MockTransport` to simulate network behavior
2. **Circuit breakers**: Tests circuit breaker state transitions
3. **Retry validation**: Verifies retry counts and exponential backoff
4. **Error message clarity**: Ensures errors are actionable

**Example chaos test:**

```python
from unittest.mock import patch
import httpx
import pytest
from asap.transport.client import ASAPClient, ASAPConnectionError

async def test_intermittent_failure(sample_envelope, response_envelope):
    """Test client resilience to intermittent failures."""
    call_count = 0

    def mock_transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # First 2 attempts fail, 3rd succeeds
        if call_count <= 2:
            raise httpx.ConnectError("Network flaky")
        return create_mock_response(response_envelope)

    with patch("asap.transport.client.asyncio.sleep"):
        async with ASAPClient(
            "http://localhost:8000",
            transport=httpx.MockTransport(mock_transport),
            max_retries=5,
        ) as client:
            response = await client.send(sample_envelope)
            assert response.payload_type == "task.response"

    assert call_count == 3  # 2 failures + 1 success
```

## Test Isolation Strategy

The ASAP Protocol test suite uses a **three-pronged approach** to ensure complete test isolation and prevent interference, especially from rate limiting:

### 1. Process Isolation (pytest-xdist)

We use `pytest-xdist` to run tests in separate processes, providing complete isolation at the process level. This is the primary mechanism for preventing interference.

**Usage**: use the fast loop in [Canonical commands](../.cursor/README.md#canonical-commands). Pass `-n 4` instead of `-n auto` when you want a fixed worker count. Keep `-m "not serial_mcp"` on any xdist run.

**Benefits**:
- Complete process-level isolation
- No shared state between test processes
- Faster execution on multi-core systems
- Automatic worker count based on CPU cores

### 2. Aggressive Monkeypatch Fixtures

For tests that can't benefit from process isolation (or need additional isolation), we use "aggressive monkeypatch" fixtures that replace module-level rate limiters.

**Why "Aggressive Monkeypatch"?**

The `slowapi.Limiter` library maintains global state that persists across tests, even with unique storage URIs. Simply replacing `app.state.limiter` is not sufficient because:

- The limiter is created at module import time
- Code may reference the module-level limiter directly
- Internal state persists even with different storage URIs

**Solution**: Our fixtures replace the limiter at the **module level** in both `asap.transport.middleware` and `asap.transport.server`, ensuring complete isolation even when code uses the global limiter directly.

**Example**:
```python
def test_rate_limiting(replace_global_limiter, isolated_limiter_factory):
    # replace_global_limiter automatically replaces module-level limiters
    limiter = isolated_limiter_factory(["5/minute"])
    # Global limiter is now replaced, app will use it automatically
    app = create_app(manifest, rate_limit="5/minute")
```

### 3. Strategic Test Organization

Tests are strategically organized to prevent interference:

- **Rate limiting tests** are in a separate file (`test_rate_limiting.py`) that runs in isolated processes
- **Non-rate-limiting tests** inherit from `NoRateLimitTestBase` to automatically disable rate limiting
- **Unit tests** have no rate limiting dependencies at all

This organization ensures that rate limiting tests don't interfere with other tests, and vice versa.

## Rate Limiting in Tests

### NoRateLimitTestBase

For tests that don't need rate limiting, inherit from `NoRateLimitTestBase`:

```python
from tests.transport.conftest import NoRateLimitTestBase

class TestMyFeature(NoRateLimitTestBase):
    """Tests for my feature without rate limiting interference."""

    def test_something(self, manifest):
        # Rate limiting is automatically disabled
        app = create_app(manifest)
        # Test your feature without rate limiting concerns
```

**What it does**:
- Automatically disables rate limiting for all tests in the class
- Replaces module-level limiters with a no-limit limiter
- Prevents interference from rate limiting tests
- No need to manually configure rate limiting

**When to use**:
- Tests that don't test rate limiting functionality
- Tests that need to make many requests without hitting limits
- Integration tests that test other features (size validation, thread pools, etc.)

### Testing Rate Limiting

When writing tests specifically for rate limiting functionality:

1. **Use aggressive monkeypatch**: Use `replace_global_limiter` or manually replace module-level limiters
2. **Isolate in separate file**: Put rate limiting tests in `test_rate_limiting.py`
3. **Use isolated limiters**: Use `isolated_limiter_factory` to create fresh limiters

**Example**:
```python
def test_rate_limit_exceeded(
    monkeypatch,
    isolated_limiter_factory,
    rate_limit_manifest,
):
    # Create isolated limiter with specific limits
    limiter = isolated_limiter_factory(["2/minute"])

    # Replace global limiter in both modules
    import asap.transport.middleware as middleware_module
    import asap.transport.server as server_module

    monkeypatch.setattr(middleware_module, "limiter", limiter)
    monkeypatch.setattr(server_module, "limiter", limiter)

    # Create app with rate limiting
    app = create_app(rate_limit_manifest, rate_limit="2/minute")
    app.state.limiter = limiter

    client = TestClient(app)

    # Make requests and verify rate limiting behavior
    # ...
```

## Choosing the Right Test Isolation Strategy

When writing transport layer tests, use this decision tree to choose the appropriate fixtures and test organization:

```mermaid
graph TD
    A[Writing Transport Test] --> B{Testing rate<br/>limiting behavior?}
    B -->|Yes| C[Create class-level fixture<br/>with aggressive monkeypatch]
    B -->|No| D[Inherit from<br/>NoRateLimitTestBase]
    
    C --> E[See test_rate_limiting.py<br/>for examples]
    C --> F{Need custom<br/>rate limit?}
    F -->|Yes| G[Create fixture like<br/>isolated_app_5_per_minute]
    F -->|No| H[Use replace_global_limiter<br/>fixture]
    
    D --> I[Rate limiting automatically<br/>disabled for all tests]
    D --> J[Can make unlimited<br/>requests without 429 errors]
    
    style C fill:#fff3cd,stroke:#856404
    style D fill:#d4edda,stroke:#155724
    style E fill:#e7f3ff,stroke:#004085
    style G fill:#e7f3ff,stroke:#004085
    style H fill:#e7f3ff,stroke:#004085
    style I fill:#d1ecf1,stroke:#0c5460
    style J fill:#d1ecf1,stroke:#0c5460
```

### Quick Reference Table

| Your Test Needs | Recommended Approach | Example |
|----------------|---------------------|---------|
| **Testing rate limiting** | Class-level fixture with aggressive monkeypatch | `test_rate_limiting.py` |
| **NOT testing rate limiting** | Inherit from `NoRateLimitTestBase` | Most integration tests |
| **Unit test (no HTTP)** | No special fixtures needed | `test_bounded_executor.py` |
| **Custom rate limit (e.g., 5/min)** | Create class-level fixture | See `isolated_app_5_per_minute` |
| **Testing other features** | `NoRateLimitTestBase` + specific fixtures | `test_request_size_limits.py` |

### When to Use Each Fixture

#### Use `NoRateLimitTestBase` when:
- ✅ Testing features OTHER than rate limiting
- ✅ Need to make many requests without hitting limits
- ✅ Testing thread pools, request size limits, metrics, etc.
- ✅ Running integration tests that involve HTTP but not rate limiting

#### Use Aggressive Monkeypatch Fixtures when:
- ✅ **Explicitly testing rate limiting behavior**
- ✅ Need precise control over rate limit values
- ✅ Testing limit thresholds, resets, or error responses
- ✅ Creating rate limiting tests in `test_rate_limiting.py`

#### Use Neither (plain pytest) when:
- ✅ Unit tests with no HTTP dependencies
- ✅ Testing pure functions or classes
- ✅ No rate limiting or app creation involved

### Example: Creating a Class-Level Fixture for Rate Limiting Tests

```python
class TestMyRateLimitFeature:
    """Tests for rate limiting with custom limits."""
    
    @pytest.fixture
    def isolated_app_custom_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        isolated_limiter_factory: Callable,
        manifest: Manifest,
    ) -> FastAPI:
        """Create app with custom rate limit."""
        # Create isolated limiter
        limiter = isolated_limiter_factory(["10/minute"])
        
        # Aggressive monkeypatch - replace in BOTH modules
        import asap.transport.middleware as middleware_module
        import asap.transport.server as server_module
        
        monkeypatch.setattr(middleware_module, "limiter", limiter)
        monkeypatch.setattr(server_module, "limiter", limiter)
        
        # Create app
        app = create_app(manifest, rate_limit="10/minute")
        app.state.limiter = limiter
        
        return app
    
    def test_my_rate_limit_feature(
        self,
        isolated_app_custom_limit: FastAPI,
    ) -> None:
        """Test uses the custom rate-limited app."""
        client = TestClient(isolated_app_custom_limit)
        # Test implementation...
```

---

## Pytest Fixtures Explained

### What are Fixtures?

Fixtures are pytest's way of providing test dependencies. They:
- Set up test data or objects
- Provide reusable test components
- Ensure proper cleanup after tests
- Can be shared across multiple tests

### Factory Fixtures vs Regular Fixtures

**Regular Fixtures**: Return a single value that is reused (or recreated) for each test:
```python
@pytest.fixture
def manifest() -> Manifest:
    """Returns a single Manifest instance."""
    return Manifest(...)
```

**Factory Fixtures**: Return a function that creates new instances:
```python
@pytest.fixture
def isolated_limiter_factory():
    """Returns a function that creates new limiters."""
    def _create(limits=None):
        return Limiter(...)
    return _create
```

Factory fixtures are useful when you need multiple instances with different configurations.

### Our Specific Fixtures

#### `isolated_limiter_factory`

A factory fixture that creates isolated rate limiters with unique storage:

```python
def test_something(isolated_limiter_factory):
    # Create a limiter with specific limits
    limiter = isolated_limiter_factory(["10/minute"])
    
    # Each call creates a NEW limiter with isolated storage
    another_limiter = isolated_limiter_factory(["5/minute"])
```

**Why we created it**: Ensures each test gets a completely fresh limiter with no shared state.

#### `replace_global_limiter`

A fixture that replaces module-level limiters using aggressive monkeypatch:

```python
def test_something(replace_global_limiter):
    # Global limiter is automatically replaced
    # Any code using the module-level limiter will use the new one
    app = create_app(manifest)
```

**Why we created it**: Provides complete isolation by replacing limiters at the module level, not just at the app level.

#### `create_isolated_app`

A factory fixture that creates apps with isolated limiters:

```python
def test_something(create_isolated_app, manifest):
    # Create app with isolated limiter
    app = create_isolated_app(
        manifest,
        rate_limit="10/minute",
        use_monkeypatch=True,  # Use aggressive monkeypatch
    )
```

**Why we created it**: Simplifies app creation for tests that need complete isolation.

---

## ASAP Testing Utilities (`asap.testing`)

The `asap.testing` package provides **MockAgent**, **pytest fixtures**, **context managers**, and **custom assertions** to reduce boilerplate when testing ASAP protocol integrations. Use these utilities to simulate agents, validate envelopes, and assert task outcomes without starting real servers.

### MockAgent

`MockAgent` simulates an ASAP agent in memory. Pre-set responses per skill, record incoming requests, and optionally add delay or raise exceptions for error-path tests.

**Basic usage:**

```python
from asap.testing import MockAgent
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse

agent = MockAgent("urn:asap:agent:mock")
agent.set_response(
    "echo",
    TaskResponse(task_id="t1", status="completed").model_dump(),
)

req = Envelope(
    asap_version="0.1",
    sender="urn:asap:agent:a",
    recipient=agent.agent_id,
    payload_type="TaskRequest",
    payload=TaskRequest(conversation_id="c", skill_id="echo", input={}).model_dump(),
)
out = agent.handle(req)

assert out is not None
assert out.payload_type == "TaskResponse"
assert len(agent.requests) == 1
assert agent.requests_for_skill("echo")[0] == req
```

**Optional behavior:**

- **Default response**: `agent.set_default_response(payload)` — used when no skill-specific response is set.
- **Delay**: `agent.set_delay(0.5)` — sleep (seconds) before returning the response (e.g. for timeout tests).
- **Simulated failure**: `agent.set_failure(ValueError("error"))` — `handle()` records the request then raises; failure is cleared after one call.
- **Clear**: `agent.clear()` — clears recorded requests and all pre-set responses.

### Pytest Fixtures (asap.testing)

These fixtures are loaded via `pytest_plugins` in `tests/conftest.py`. Request them by name in your test parameters.

| Fixture | Type | Description |
|--------|------|-------------|
| `mock_agent` | `MockAgent` | Fresh MockAgent per test. |
| `mock_snapshot_store` | `InMemorySnapshotStore` | Empty in-memory snapshot store per test. |
| `mock_client` | async | `ASAPClient` already entered; use in async tests. Base URL: `http://localhost:9999`. |

**Example using fixtures:**

```python
def test_echo_roundtrip(mock_agent: MockAgent) -> None:
    mock_agent.set_response(
        "echo",
        TaskResponse(task_id="t1", status="completed").model_dump(),
    )
    req = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:a",
        recipient=mock_agent.agent_id,
        payload_type="TaskRequest",
        payload=TaskRequest(
            conversation_id="c", skill_id="echo", input={}
        ).model_dump(),
    )
    out = mock_agent.handle(req)
    assert out is not None
    assert_task_completed(out)
```

### Context Managers

Use context managers when you want an agent or client scoped to a block, with cleanup on exit.

**`test_agent(agent_id="urn:asap:agent:mock")`** — yields a `MockAgent` and calls `agent.clear()` on exit:

```python
from asap.testing.fixtures import test_agent

with test_agent("urn:asap:agent:custom") as agent:
    agent.set_response("echo", response_payload)
    out = agent.handle(req)
    assert out is not None
# agent is cleared after the block
```

**`test_client(base_url="http://localhost:9999")`** — async context manager yielding an open `ASAPClient`:

```python
from asap.testing.fixtures import test_client

async with test_client("http://localhost:8000") as client:
    response = await client.send(envelope)
    assert response is not None
```

### Custom Assertions

Use these instead of ad-hoc `assert` logic for envelopes and task status.

**`assert_envelope_valid(envelope, require_id=True, require_timestamp=True, allowed_payload_types=None)`**

Checks that the envelope has required fields and, optionally, that `payload_type` is in `allowed_payload_types`. Fails with clear messages.

```python
from asap.testing import assert_envelope_valid

assert_envelope_valid(response_envelope)
assert_envelope_valid(
    response_envelope,
    allowed_payload_types=["TaskResponse", "TaskUpdate"],
)
```

**`assert_task_completed(payload, status_key="status", completed_value="completed")`**

Asserts that a task response (dict or Envelope) indicates completion. Accepts either a payload dict or an Envelope (uses `envelope.payload`).

```python
from asap.testing import assert_task_completed

assert_task_completed({"status": "completed"})
assert_task_completed(response_envelope)  # envelope.payload must have status=completed
```

**`assert_response_correlates(request_envelope, response_envelope, correlation_id_field="correlation_id")`**

Asserts that the response’s correlation id matches the request’s id.

```python
from asap.testing import assert_response_correlates

assert_response_correlates(request_envelope, response_envelope)
```

### Reducing Test Boilerplate

**Before** (manual setup and assertions):

```python
def test_echo_response():
    agent_id = "urn:asap:agent:echo"
    responses = {}
    responses["echo"] = {"task_id": "t1", "status": "completed"}
    req = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:a",
        recipient=agent_id,
        payload_type="TaskRequest",
        payload={"conversation_id": "c", "skill_id": "echo", "input": {}},
    )
    # ... manual handling and building response envelope ...
    assert response is not None
    assert response.payload_type == "TaskResponse"
    assert response.sender == agent_id
    assert response.payload.get("status") == "completed"
    assert response.correlation_id == req.id
```

**After** (using MockAgent and assertions):

```python
def test_echo_response(mock_agent: MockAgent) -> None:
    mock_agent.set_response(
        "echo",
        TaskResponse(task_id="t1", status="completed").model_dump(),
    )
    req = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:a",
        recipient=mock_agent.agent_id,
        payload_type="TaskRequest",
        payload=TaskRequest(
            conversation_id="c", skill_id="echo", input={}
        ).model_dump(),
    )
    out = mock_agent.handle(req)

    assert_envelope_valid(out, allowed_payload_types=["TaskResponse"])
    assert_task_completed(out)
    assert_response_correlates(req, out)
```

Using `mock_agent`, `assert_envelope_valid`, `assert_task_completed`, and `assert_response_correlates` keeps tests shorter and failures easier to interpret.

### How Fixtures Provide Test Isolation

Fixtures ensure test isolation by:
1. **Creating fresh instances**: Each test gets its own instances of fixtures
2. **Using unique identifiers**: UUIDs and unique storage URIs prevent state sharing
3. **Replacing global state**: Monkeypatch fixtures replace module-level state
4. **Automatic cleanup**: Pytest automatically cleans up fixtures after tests

## Additional test commands

### Parallel Execution

Run tests in parallel with pytest-xdist via the [canonical commands](../.cursor/README.md#canonical-commands).

Do **not** add `--cov` to pytest `addopts`. A partial file run would fail the project-wide floor (see [CI Integration](#ci-integration)).

**Benefits**:
- Faster execution on multi-core systems
- Complete process-level isolation
- Automatic worker count based on CPU cores

### Running by Test Type

Run only unit tests:
```bash
uv run pytest tests/transport/unit/
```

Run only integration tests:
```bash
uv run pytest tests/transport/integration/
```

Run only E2E tests:
```bash
uv run pytest tests/transport/e2e/
```

### Verbose Output

For more detailed output:
```bash
# Verbose mode
uv run pytest -v

# Very verbose (shows each test name)
uv run pytest -vv

# Show print statements
uv run pytest -s
```

## Writing New Tests

### Choosing the Right Directory

1. **Unit tests** (`tests/transport/unit/`):
   - Test individual classes/functions
   - No HTTP dependencies
   - No rate limiting dependencies
   - Fast and deterministic

2. **Integration tests** (`tests/transport/integration/`):
   - Test component interactions
   - May use HTTP clients
   - Use `NoRateLimitTestBase` if not testing rate limiting
   - Use aggressive monkeypatch if testing rate limiting

3. **E2E tests** (`tests/transport/e2e/`):
   - Test complete workflows
   - Use full stack
   - Inherit from `NoRateLimitTestBase`

### Choosing the Right Base Class

**For non-rate-limiting tests**:
```python
from tests.transport.conftest import NoRateLimitTestBase

class TestMyFeature(NoRateLimitTestBase):
    """Tests automatically have rate limiting disabled."""
    pass
```

**For rate limiting tests**:
```python
# Don't inherit from NoRateLimitTestBase
# Use aggressive monkeypatch instead
def test_rate_limiting(monkeypatch, isolated_limiter_factory):
    # Manual limiter replacement
    pass
```

### Test Structure

Follow this structure for new tests:

```python
"""Brief description of what this test module covers."""

import pytest
from fastapi.testclient import TestClient

from tests.transport.conftest import NoRateLimitTestBase


class TestMyFeature(NoRateLimitTestBase):
    """Tests for my feature."""

    @pytest.fixture
    def manifest(self) -> Manifest:
        """Create test manifest."""
        return Manifest(...)

    def test_specific_behavior(self, manifest: Manifest) -> None:
        """Test a specific behavior."""
        # Arrange
        app = create_app(manifest)
        client = TestClient(app)

        # Act
        response = client.post("/asap", json={...})

        # Assert
        assert response.status_code == 200
        assert response.json()["result"] == expected_value
```

### Best Practices

1. **Use type hints**: All test functions should have type annotations
2. **Descriptive names**: Test names should clearly describe what they test
3. **One assertion per concept**: Group related assertions, but test one concept per test
4. **Use fixtures**: Don't duplicate setup code, use fixtures
5. **Isolation**: Each test should be independent and runnable in isolation
6. **Fast tests**: Keep tests fast (< 1 second when possible)
7. **Deterministic**: Tests should produce the same results every time

## Troubleshooting

### Skipped Tests (Brotli)

Some compression tests are conditional on the optional **brotli** package:

- **When brotli is installed** (default with `uv sync --extra dev`): Tests that require brotli run (compress/decompress, server brotli). A few tests that verify the "brotli unavailable" path are skipped (they run only in envs without brotli).
- **When brotli is not installed**: The brotli-dependent tests are skipped; the "unavailable" tests run.

To run all brotli-related tests, install dev dependencies (brotli is in `[project.optional-dependencies] dev`). The remaining skips are intentional: they assert behavior when brotli is missing.

### Tests Failing with HTTP 429 (Rate Limit Exceeded)

**Symptom**: Tests fail with "429 Too Many Requests" even when not testing rate limiting.

**Solution**:
1. Inherit from `NoRateLimitTestBase`:
   ```python
   class TestMyFeature(NoRateLimitTestBase):
       pass
   ```

2. Or use `replace_global_limiter` fixture:
   ```python
   def test_something(replace_global_limiter):
       # Rate limiting is now isolated
       pass
   ```

### Tests Interfering with Each Other

**Symptom**: Tests pass individually but fail when run together.

**Solution**:
1. Ensure tests use isolated fixtures (`isolated_limiter_factory`, `replace_global_limiter`)
2. Run with pytest-xdist for process isolation, using the fast loop in [Canonical commands](../.cursor/README.md#canonical-commands)
3. Check that rate limiting tests are in `test_rate_limiting.py`
4. Verify non-rate-limiting tests inherit from `NoRateLimitTestBase`

### Slow Test Execution

**Solution**:
1. Use the fast loop in [Canonical commands](../.cursor/README.md#canonical-commands)
2. Run only relevant tests during development
3. Use `pytest --lf` to run only failed tests from last run
4. Use `pytest --ff` to run failed tests first, then others

## CI Integration

CI (`.github/workflows/ci.yml`) collects coverage in the test job. The parallel run excludes `serial_mcp`; a second in-process run appends that marker with `--cov-append`. The coverage job enforces the floor with `uv run coverage report --fail-under=85` and uploads the report.

The argv is the CI block in [Canonical commands](../.cursor/README.md#canonical-commands). Local pre-push is the local floor in that same section.

See `.github/workflows/ci.yml` for linting, formatting, type checking, and security steps.
