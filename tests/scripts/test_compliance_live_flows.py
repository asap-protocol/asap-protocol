"""Tests for scripts/compliance_live_flows.py, the CI job's logic."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.compliance_live_flows import (
    LiveFlowError,
    ensure_port_free,
    start_server,
    stop_server,
    wait_until_ready,
)


def _start_python(tmp_path: Path, code: str) -> tuple[subprocess.Popen[bytes], Path]:
    log = tmp_path / "server.log"
    return start_server([sys.executable, "-c", code], env=dict(os.environ), log_path=log), log


def _wait_for(condition: Callable[[], object], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_a_server_that_exits_first_fails_with_its_exit_code_and_log(tmp_path: Path) -> None:
    process, log = _start_python(tmp_path, "print('address already in use'); raise SystemExit(3)")

    with pytest.raises(LiveFlowError, match=r"(?s)exited with code 3.*address already in use"):
        wait_until_ready(process, lambda: False, name="gateway", log_path=log, timeout=10)


def test_a_server_that_never_answers_fails_after_the_time_box(tmp_path: Path) -> None:
    process, log = _start_python(tmp_path, "import time; time.sleep(60)")
    try:
        with pytest.raises(LiveFlowError, match="mcp did not answer within 0.3 s"):
            wait_until_ready(process, lambda: False, name="mcp", log_path=log, timeout=0.3)
    finally:
        stop_server(process)


def test_a_server_that_answers_is_ready_and_stops_on_sigterm(tmp_path: Path) -> None:
    process, log = _start_python(tmp_path, "import time; time.sleep(60)")
    answers = iter([False, True])

    wait_until_ready(process, lambda: next(answers), name="mcp", log_path=log, timeout=10)
    stop_server(process)

    assert process.returncode == -signal.SIGTERM


def test_stopping_a_server_ends_its_children_too(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    command = ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"]
    process = start_server(command, env=dict(os.environ), log_path=tmp_path / "log")
    assert _wait_for(lambda: pid_file.exists() and pid_file.read_text().strip())
    child = int(pid_file.read_text())

    stop_server(process)

    assert _wait_for(lambda: not _alive(child))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_server_that_ignores_sigterm_is_killed_after_the_grace(tmp_path: Path) -> None:
    code = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('in', flush=True); time.sleep(60)"
    )
    process, log = _start_python(tmp_path, code)
    assert _wait_for(lambda: "in" in log.read_text())

    stop_server(process, grace=0.2)

    assert process.returncode == -signal.SIGKILL


def test_a_port_in_use_is_refused_before_a_server_starts() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        with pytest.raises(LiveFlowError, match=f"127.0.0.1:{port} is in use"):
            ensure_port_free(port)
