#!/usr/bin/env python3
"""Run both COMPAT-007 compliance profiles against the two example servers.

The CI job ``compliance (live flows)`` runs this after the pnpm install and builds. It
starts ``examples/a2a_gateway`` and ``examples/mcp_auth_http`` in their test-only
compliance mode on 127.0.0.1, waits until each answers, runs the live tests of
``asap-compliance`` against them, and always stops both servers. It fails on a taken
port, on a server that exits or does not answer in time (with its log), and on a live
profile test that did not pass, a skip included: each of those runs its profile twice
in a row against one server start.

Example: ``uv run python scripts/compliance_live_flows.py --a2a-port 18788 --mcp-port 18787``
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
# As pytest reports them (classname::name, rootdir asap-compliance/).
REQUIRED_LIVE_TESTS = (
    "tests.test_a2a_bridge.TestLiveGateway::test_the_profile_passes_twice_in_a_row",
    "tests.test_mcp_auth_http.TestLiveExampleServer::test_the_profile_passes_twice_in_a_row",
)


class LiveFlowError(RuntimeError):
    """The live run cannot go on or did not pass; the text says why."""


def ensure_port_free(port: int) -> None:
    """Refuse a taken port: a server already on it would answer the readiness check."""
    with socket.socket() as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            raise LiveFlowError(f"{HOST}:{port} is in use; pick another port") from None


def start_server(
    command: Sequence[str], *, env: dict[str, str], log_path: Path
) -> subprocess.Popen[bytes]:
    """Start *command* in its own process group, with its output in *log_path*."""
    with log_path.open("wb") as log:
        return subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def wait_until_ready(
    process: subprocess.Popen[bytes],
    is_ready: Callable[[], bool],
    *,
    name: str,
    log_path: Path,
    timeout: float,
) -> None:
    """Return once *is_ready* holds; fail when the server exits first or *timeout* passes."""
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if is_ready():
            return
        if time.monotonic() >= deadline:
            raise LiveFlowError(f"{name} did not answer within {timeout} s\n{_log(log_path)}")
        time.sleep(0.25)
    raise LiveFlowError(f"{name} exited with code {process.returncode}\n{_log(log_path)}")


def stop_server(process: subprocess.Popen[bytes], *, grace: float = 10.0) -> None:
    """SIGTERM the server's process group, then SIGKILL whatever is left after *grace*."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)
    # pnpm and uv run the server as a child: it must not outlive the job.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def live_tests_not_passed(junit_path: Path) -> list[str]:
    """The tests of ``REQUIRED_LIVE_TESTS`` that the JUnit report does not show as passed."""
    passed = {
        f"{case.get('classname')}::{case.get('name')}"
        for case in ElementTree.parse(junit_path).iter("testcase")
        if not any(child.tag in {"failure", "error", "skipped"} for child in case)
    }
    return [test_id for test_id in REQUIRED_LIVE_TESTS if test_id not in passed]


def _log(log_path: Path) -> str:
    return f"--- {log_path.name} ---\n{log_path.read_text(encoding='utf-8', errors='replace')}"


def _answers_200(url: str) -> Callable[[], bool]:
    def is_ready() -> bool:
        try:
            return httpx.get(url, timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    return is_ready


def _run(a2a_port: int, mcp_port: int, wait: float, workdir: Path) -> None:
    a2a_origin, mcp_origin = f"http://{HOST}:{a2a_port}", f"http://{HOST}:{mcp_port}"
    gateway = f"-m examples.a2a_gateway.serve --host {HOST} --port {a2a_port} --compliance"
    servers = {  # name: (command, extra environment, URL that answers 200 once it is up)
        "a2a_gateway": (
            [sys.executable, *gateway.split()],
            {},
            f"{a2a_origin}/.well-known/agent-card.json",
        ),
        "mcp_auth_http": (
            ["pnpm", "--filter", "example-mcp-auth-http", "start", "--", "--compliance"],
            {"PORT": str(mcp_port)},
            f"{mcp_origin}/.well-known/oauth-protected-resource/mcp",
        ),
    }
    for port in (a2a_port, mcp_port):
        ensure_port_free(port)
    started: list[subprocess.Popen[bytes]] = []
    try:
        for name, (command, extra_env, url) in servers.items():
            log = workdir / f"{name}.log"
            started.append(start_server(command, env={**os.environ, **extra_env}, log_path=log))
            wait_until_ready(started[-1], _answers_200(url), name=name, log_path=log, timeout=wait)
            print(f"{name} answers at {url}", flush=True)
        try:
            _run_live_tests(a2a_origin, f"{mcp_origin}/mcp", workdir / "live.xml")
        except LiveFlowError:
            for name in servers:  # the server side of a failed test
                print(_log(workdir / f"{name}.log"), file=sys.stderr)
            raise
    finally:
        for process in started:
            stop_server(process)


def _run_live_tests(a2a_origin: str, mcp_url: str, junit_path: Path) -> None:
    urls = {"ASAP_COMPLIANCE_A2A_GATEWAY_URL": a2a_origin, "ASAP_COMPLIANCE_MCP_HTTP_URL": mcp_url}
    markers = "a2a_gateway_live or mcp_http_live"
    command = [sys.executable, "-m", "pytest", "asap-compliance/tests/", "-m", markers, "-v"]
    env = {**os.environ, **urls}
    run = subprocess.run([*command, f"--junitxml={junit_path}"], cwd=REPO_ROOT, env=env)
    if run.returncode != 0:
        raise LiveFlowError(f"the live tests failed (pytest exit code {run.returncode})")
    if not_passed := live_tests_not_passed(junit_path):
        raise LiveFlowError(f"these live tests did not pass (skipped or missing): {not_passed}")
    print(f"each profile passed twice in a row: {', '.join(REQUIRED_LIVE_TESTS)}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run both compliance profiles live.")
    parser.add_argument("--a2a-port", type=int, default=8788, help="examples/a2a_gateway port")
    parser.add_argument("--mcp-port", type=int, default=8787, help="examples/mcp_auth_http port")
    parser.add_argument("--ready-timeout", type=float, default=60.0, help="seconds per server")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="compliance-live-") as workdir:
        try:
            _run(args.a2a_port, args.mcp_port, args.ready_timeout, Path(workdir))
        except LiveFlowError as exc:
            print(f"compliance live flows failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
