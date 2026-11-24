# ASAP Protocol Performance Benchmarks

This directory contains performance benchmarks for the ASAP protocol implementation.

## Prerequisites

Install pytest-benchmark (included in dev dependencies):

```bash
uv sync --extra dev
```

## Running Benchmarks

### Run all benchmarks

Run the timing measurements and the four asynchronous transport tests together.

```bash
uv run pytest benchmarks/benchmark_*.py -v
```

### Run Specific Benchmark File

```bash
# Model serialization benchmarks
uv run pytest benchmarks/benchmark_models.py --benchmark-only -v

# Transport layer benchmarks
uv run pytest benchmarks/benchmark_transport.py --benchmark-only -v
```

### Run Specific Benchmark Class

```bash
uv run pytest benchmarks/benchmark_models.py::TestEnvelopeCreation --benchmark-only -v
```

## Benchmark Categories

### Model Benchmarks (`benchmark_models.py`)

| Category | Description | Target |
|----------|-------------|--------|
| Envelope Creation | Create envelopes with auto-generated fields | < 100μs |
| JSON Serialization | Convert models to JSON dict/string | < 50μs |
| JSON Deserialization | Parse JSON into validated models | < 100μs |
| Entity Creation | Create various entity types | < 50μs |
| Batch Operations | Process 100 items in sequence | < 10ms |

### Transport Benchmarks (`benchmark_transport.py`)

| Category | Description | Target |
|----------|-------------|--------|
| JSON-RPC Processing | Create/serialize JSON-RPC messages | < 1ms |
| Manifest Endpoint | GET manifest discovery | < 1ms |
| Metrics Endpoint | GET Prometheus metrics | < 2ms |
| ASAP Endpoint | POST message processing | < 5ms |
| Throughput | Sequential request batches | > 100 req/s |
| Payload Sizes | Small/medium/large payloads | Linear scaling |

## Output Options

### Compare Against Baseline

Save a baseline for future comparisons:

```bash
# Save baseline
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-save=baseline

# Compare against baseline
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-compare=baseline
```

### Generate Reports

```bash
# JSON report
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-json=benchmark-results.json

# Histogram output
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-histogram=benchmark-hist
```

### Calibration Options

```bash
# More iterations for stability
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-min-rounds=100

# Disable warmup (for quick checks)
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-warmup=off

# Quick run (fewer iterations)
uv run pytest benchmarks/benchmark_*.py --benchmark-only --benchmark-disable-gc --benchmark-min-rounds=5
```

## Interpreting Results

The benchmark output shows:

- **min**: Minimum time observed
- **max**: Maximum time observed  
- **mean**: Average time
- **stddev**: Standard deviation
- **median**: Middle value
- **ops**: Operations per second

Example output:

```
-------------------------------- benchmark: 5 tests --------------------------------
Name                              Min      Max     Mean   StdDev   Median     OPS
test_envelope_creation_minimal   45μs    120μs    55μs     8μs     52μs   18,181
test_envelope_to_json            12μs     35μs    15μs     3μs     14μs   66,666
test_manifest_endpoint          0.8ms    2.1ms   1.0ms   0.2ms   0.9ms    1,000
```

## Performance Targets

The following targets represent acceptable performance for production use:

| Operation | Target | Rationale |
|-----------|--------|-----------|
| Envelope creation | < 100μs | Should not add noticeable latency |
| JSON serialization | < 50μs | Fast serialization enables high throughput |
| HTTP round-trip | < 10ms | Local processing overhead only |
| Throughput | > 100 req/s | Minimum for low-traffic agents |

## CI Integration

Benchmarks can be integrated into CI to detect performance regressions:

```yaml
# .github/workflows/benchmark.yml
- name: Run benchmarks
  run: |
    uv run pytest benchmarks/benchmark_*.py --benchmark-only \
      --benchmark-json=benchmark-results.json
    
- name: Store benchmark results
  uses: actions/upload-artifact@v4
  with:
    name: benchmark-results
    path: benchmark-results.json
```

---

## Load Testing with Locust

The `load_test.py` file provides production-grade load testing using Locust.

### Performance Targets

| Metric | Target | Description |
|--------|--------|-------------|
| RPS | 1000 req/sec | Sustained throughput |
| p95 Latency | < 5ms | 95th percentile response time |
| Error Rate | < 0.1% | Maximum acceptable failure rate |

### Running Load Tests

1. **Start the ASAP server** in one terminal:

```bash
uv run uvicorn asap.transport.server:app --host 0.0.0.0 --port 8000
```

2. **Run load test** in another terminal:

```bash
# Headless mode (CI/automated)
uv run locust -f benchmarks/load_test.py --headless \
    -u 100 -r 10 -t 60s --host http://localhost:8000

# Web UI mode (interactive)
uv run locust -f benchmarks/load_test.py --host http://localhost:8000
# Then open http://localhost:8089 in browser
```

### Command-Line Options

| Option | Description | Example |
|--------|-------------|---------|
| `-u` | Number of concurrent users | `-u 100` |
| `-r` | Spawn rate (users/second) | `-r 10` |
| `-t` | Test duration | `-t 60s` or `-t 5m` |
| `--host` | Target server URL | `--host http://localhost:8000` |
| `--html` | Generate HTML report | `--html=report.html` |
| `--csv` | Generate CSV output | `--csv=results` |

### User Classes

The load test includes two user classes:

1. **ASAPLoadTestUser** (default): Mixed workload with task requests, manifest fetches, and metrics checks
2. **ASAPSustainedLoadUser**: Maximum throughput testing with minimal wait time

```bash
# Use sustained load user for max throughput testing
uv run locust -f benchmarks/load_test.py --headless \
    -u 500 -r 50 -t 60s --host http://localhost:8000 \
    --class-picker ASAPSustainedLoadUser
```

### Output

The load test outputs:
- Real-time console statistics during the test
- Final summary with latency percentiles (p50, p95, p99)
- Target verification (PASS/FAIL for each target)

Example output:
```
AGGREGATE RESULTS:
Total requests: 60,000
Total failures: 5
Error rate: 0.008%
Aggregate RPS: 1,000.00
Aggregate p95: 3.45ms

TARGET VERIFICATION:
RPS >= 800: PASS (1000.00)
p95 <= 5ms: PASS (3.45ms)
Error rate <= 0.1%: PASS (0.008%)

✅ ALL TARGETS MET
```

---

## Stress Testing with Locust

The `stress_test.py` file provides stress testing to find the server's breaking point.

### Goals

| Goal | Description |
|------|-------------|
| Breaking point | Find max RPS before failures |
| Degradation curve | Measure latency vs load |
| Resource limits | Identify exhaustion thresholds |

### Breaking Point Thresholds

| Metric | Threshold | Status |
|--------|-----------|--------|
| Error rate | >= 5% | Breaking point |
| p95 latency | >= 100ms | Degraded |
| p99 latency | >= 500ms | Severe degradation |

### Running Stress Tests

1. **Start the ASAP server** in one terminal:

```bash
uv run uvicorn asap.transport.server:app --host 0.0.0.0 --port 8000
```

2. **Run stress test** in another terminal:

```bash
# Step-load stress test (recommended)
uv run locust -f benchmarks/stress_test.py --headless \
    -u 500 -r 50 -t 5m --host http://localhost:8000 \
    --step-load --step-users 50 --step-time 30s

# Spike test (sudden high load)
uv run locust -f benchmarks/stress_test.py --headless \
    -u 1000 -r 500 -t 2m --host http://localhost:8000

# Web UI mode (interactive)
uv run locust -f benchmarks/stress_test.py --host http://localhost:8000
```

### User Classes

1. **StressTestUser**: Maximum throughput with minimal wait time
2. **BurstTestUser**: Simulates traffic bursts followed by quiet periods

### Output

The stress test outputs:
- Degradation curve (users vs RPS vs latency vs errors)
- Breaking point detection with reason
- Max sustainable RPS recommendation

Example output:
```
BREAKING POINT ANALYSIS:
Breaking point detected!
  RPS at break: 1250.00
  Users at break: 150
  Reason: Error rate 5.2% >= 5%

Max sustainable RPS: 1100.00
Recommended capacity: 880.00 RPS (80% of max)
```

---

## Memory Leak Detection

The `memory_test.py` file provides long-duration memory profiling to detect leaks.

### Goals

| Goal | Description |
|------|-------------|
| Leak detection | Identify memory growth over time |
| Trend analysis | Monitor memory usage patterns |
| Component profiling | Test individual components |

### Leak Thresholds

| Metric | Threshold | Status |
|--------|-----------|--------|
| Growth rate | < 10 MB/hour | Normal |
| Growth rate | 10-50 MB/hour | Monitor |
| Growth rate | > 50 MB/hour | Leak detected |

### Running Memory Tests

```bash
# Quick test (5 minutes)
uv run python benchmarks/memory_test.py --duration 300

# Full test (1 hour)
uv run python benchmarks/memory_test.py --duration 3600

# Custom RPS
uv run python benchmarks/memory_test.py --duration 600 --rps 200

# Component tests only (faster)
uv run python benchmarks/memory_test.py --components-only
```

### Command-Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--duration` | Test duration in seconds | 3600 (1 hour) |
| `--interval` | Sample interval in seconds | 10 |
| `--rps` | Target requests per second | 100 |
| `--components-only` | Run component tests only | False |

### Output

The memory test outputs:
- Memory samples over time
- Growth rate analysis (MB/hour)
- Leak detection verdict

Example output:
```
MEMORY USAGE:
Initial: 45.00 MB
Final: 52.00 MB
Peak: 55.00 MB
Growth: +7.00 MB
Growth Rate: +7.00 MB/hour

ANALYSIS:
Trend: stable (no leak detected)

✅ No memory leak detected
```

---

## Troubleshooting

### High Variance in Results

If results show high variance:

1. Close other applications to reduce system load
2. Increase `--benchmark-min-rounds` for more samples
3. Use `--benchmark-disable-gc` to eliminate GC pauses
4. Run on a quiet machine (not during heavy CI load)

### Slow Benchmark Runs

For faster feedback during development:

```bash
uv run pytest benchmarks/benchmark_*.py --benchmark-only \
  --benchmark-warmup=off \
  --benchmark-min-rounds=3 \
  -x  # Stop on first failure
```
