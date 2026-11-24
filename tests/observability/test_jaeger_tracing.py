"""Integration test for OpenTelemetry tracing with Jaeger.

Verifies that when OTEL_TRACES_EXPORTER=otlp and OTEL_EXPORTER_OTLP_ENDPOINT
point to a running Jaeger, spans from ASAP server (handler, request) appear
in Jaeger. Requires Docker to run Jaeger all-in-one.

Run with: pytest tests/observability/test_jaeger_tracing.py -v
Skip if Docker is not available: test is skipped when docker or Jaeger cannot be started.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

# Default manifest id used when running uvicorn asap.transport.server:app
DEFAULT_SERVICE_NAME = "urn:asap:agent:default-server"
ASAP_SERVER_PORT = 8765


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _published_port(container_id: str, container_port: int) -> int:
    """Read the host port that Docker assigned to this container."""
    result = subprocess.run(
        ["docker", "port", container_id, f"{container_port}/tcp"],
        capture_output=True,
        check=True,
        text=True,
        timeout=5,
    )
    host, _, port = result.stdout.strip().rpartition(":")
    assert host == "127.0.0.1", "Jaeger must bind to localhost"
    return int(port)


@pytest.fixture
def jaeger_urls(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """Start an isolated collector and remove only its container."""
    if not _docker_available():
        pytest.skip("Docker not available")
    cidfile = tmp_path / "jaeger.cid"
    try:
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--cidfile",
                    str(cidfile),
                    "--name",
                    f"asap-jaeger-tracing-test-{uuid4().hex}",
                    "--publish",
                    "127.0.0.1::16686",
                    "--publish",
                    "127.0.0.1::4317",
                    "jaegertracing/all-in-one:1.53",
                ],
                capture_output=True,
                check=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"Could not start Jaeger: {type(exc).__name__}")

        container_id = cidfile.read_text().strip()
        ui_port = _published_port(container_id, 16686)
        otlp_port = _published_port(container_id, 4317)
        yield f"http://127.0.0.1:{ui_port}", f"http://127.0.0.1:{otlp_port}"
    finally:
        if cidfile.is_file() and (container_id := cidfile.read_text().strip()):
            subprocess.run(
                ["docker", "rm", "--force", container_id], capture_output=True, timeout=10
            )


def _wait_for_jaeger_ui(jaeger_url: str, timeout_seconds: int = 30) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            r = httpx.get(jaeger_url, timeout=2.0, trust_env=False)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


def _wait_for_asap_server(timeout_seconds: int = 15) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{ASAP_SERVER_PORT}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _send_asap_request() -> bool:
    envelope = {
        "asap_version": "0.1",
        "sender": "urn:asap:agent:client",
        "recipient": "urn:asap:agent:default-server",
        "payload_type": "task.request",
        "payload": {
            "conversation_id": "conv-jaeger-test",
            "skill_id": "echo",
            "input": {"message": "jaeger test"},
        },
    }
    body = {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope},
        "id": "jaeger-test-1",
    }
    try:
        r = httpx.post(
            f"http://127.0.0.1:{ASAP_SERVER_PORT}/asap",
            json=body,
            timeout=5.0,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _query_jaeger_traces(jaeger_url: str, service: str) -> list[dict]:
    try:
        url = f"{jaeger_url}/api/traces"
        r = httpx.get(url, params={"service": service}, timeout=5.0, trust_env=False)
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("data") or []
    except httpx.HTTPError:
        return []


def _wait_for_jaeger_traces(jaeger_url: str, service: str, timeout_seconds: int = 15) -> list[dict]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        traces = _query_jaeger_traces(jaeger_url, service)
        if len(traces) >= 1:
            return traces
        time.sleep(1.0)
    return []


def test_jaeger_receives_asap_traces(jaeger_urls: tuple[str, str]) -> None:
    """Start the ASAP server with OTLP and check that Jaeger receives a trace."""
    jaeger_url, otlp_url = jaeger_urls
    if not _wait_for_jaeger_ui(jaeger_url):
        pytest.skip("Jaeger UI did not become ready")

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src_path = os.path.join(project_root, "src")
    env = os.environ.copy()
    env["OTEL_TRACES_EXPORTER"] = "otlp"
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_url
    env["PYTHONPATH"] = src_path

    # Start ASAP server (uvicorn asap.transport.server:app) with same Python as pytest
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "asap.transport.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(ASAP_SERVER_PORT),
        ],
        env=env,
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_asap_server():
            pytest.skip("ASAP server did not become ready")
        if not _send_asap_request():
            pytest.skip("ASAP request failed")

        # OTLP uses batch export; poll until traces appear or timeout.
        # Multiple traces may exist (e.g. health check). Find one with ASAP handler span.
        traces = _wait_for_jaeger_traces(jaeger_url, DEFAULT_SERVICE_NAME, timeout_seconds=15)
        assert len(traces) >= 1, (
            f"Expected at least one trace for service {DEFAULT_SERVICE_NAME} in Jaeger; "
            f"got {len(traces)}. Check Jaeger UI at {jaeger_url}"
        )
        handler_trace = None
        for trace in traces:
            spans = trace.get("spans") or []
            span_names = {s.get("operationName") for s in spans}
            if "asap.handler.execute" in span_names:
                handler_trace = trace
                break
        assert handler_trace is not None, (
            f"Expected at least one trace with span asap.handler.execute; "
            f"got traces with span sets: {[{s.get('operationName') for s in (t.get('spans') or [])} for t in traces]}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
