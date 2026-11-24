#!/usr/bin/env python3
"""Run both COMPAT-007 compliance profiles against the two example servers.

The CI job ``compliance (live flows)`` starts ``examples/a2a_gateway`` and
``examples/mcp_auth_http`` in their test-only compliance mode on 127.0.0.1, waits until
each answers, runs the live tests of ``asap-compliance`` against them, and always stops
both servers. This module holds the server lifecycle: a port check, the start in a
process group with a log file, a time-boxed wait that fails with the log of a server
that exits or does not answer, and a stop that ends the whole process group.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"


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


def _log(log_path: Path) -> str:
    return f"--- {log_path.name} ---\n{log_path.read_text(encoding='utf-8', errors='replace')}"
