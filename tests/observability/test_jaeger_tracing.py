"""Verify that an ASAP handler span reaches a local Jaeger over OTLP.

Run with: uv run pytest tests/observability/test_jaeger_tracing.py -v
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

DEFAULT_SERVICE_NAME = "urn:asap:agent:default-server"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASAP_LISTENER = re.compile(r"Uvicorn running on (http://127\.0\.0\.1:\d+)")


def _docker_available() -> bool:
    """Return whether the local Docker daemon can respond."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
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


@contextmanager
def _running_asap(otlp_url: str, log_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """Start the default app on a port the OS assigns."""
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("OTEL_")},
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_url,
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "asap.transport.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--no-use-colors",
            ],
            env=env,
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


async def _asap_url(proc: subprocess.Popen[bytes], log_path: Path) -> str:
    """Read the listener URL after app startup, or report a startup failure."""
    try:
        async with asyncio.timeout(15):
            while proc.poll() is None:
                if match := ASAP_LISTENER.search(log_path.read_text()):
                    return match.group(1)
                await asyncio.sleep(0.1)
    except TimeoutError:
        pass
    pytest.fail(f"ASAP server did not start listening:\n{log_path.read_text()}")


async def _wait_for_jaeger_ui(client: httpx.AsyncClient, url: str) -> None:
    """Wait for the collector UI before starting the server."""
    try:
        async with asyncio.timeout(30):
            while True:
                try:
                    if (await client.get(url)).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
    except TimeoutError:
        pytest.skip("Jaeger UI did not become ready")


def _echo_request() -> dict[str, Any]:
    """Return a valid echo task for the default app."""
    envelope = {
        "asap_version": "0.1",
        "sender": "urn:asap:agent:client",
        "recipient": DEFAULT_SERVICE_NAME,
        "payload_type": "task.request",
        "payload": {
            "conversation_id": "conv-jaeger-test",
            "skill_id": "echo",
            "input": {"message": "jaeger test"},
        },
    }
    return {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope},
        "id": "jaeger-test-1",
    }


async def _wait_for_handler_span(client: httpx.AsyncClient, jaeger_url: str, trace_id: str) -> None:
    """Wait for the exported handler span from the request under test."""
    try:
        async with asyncio.timeout(15):
            while True:
                response = await client.get(
                    f"{jaeger_url}/api/traces", params={"service": DEFAULT_SERVICE_NAME}
                )
                response.raise_for_status()
                traces = response.json()["data"] or []
                for trace in traces:
                    if trace["traceID"] == trace_id and any(
                        span["operationName"] == "asap.handler.execute" for span in trace["spans"]
                    ):
                        return
                await asyncio.sleep(0.2)
    except TimeoutError:
        pytest.fail(f"Jaeger did not receive asap.handler.execute for trace {trace_id}")


async def test_jaeger_receives_asap_traces(tmp_path: Path, jaeger_urls: tuple[str, str]) -> None:
    """Check that Jaeger receives the handler span from a successful echo call."""
    jaeger_url, otlp_url = jaeger_urls
    log_path = tmp_path / "asap-server.log"
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        await _wait_for_jaeger_ui(client, jaeger_url)
        with _running_asap(otlp_url, log_path) as proc:
            asap_url = await _asap_url(proc, log_path)
            response = await client.post(f"{asap_url}/asap", json=_echo_request())
            assert response.status_code == 200, response.text
            body = response.json()
            assert "error" not in body, body
            envelope = body["result"]["envelope"]
            assert envelope["payload"]["result"]["echoed"] == {"message": "jaeger test"}
            await _wait_for_handler_span(client, jaeger_url, envelope["trace_id"])
