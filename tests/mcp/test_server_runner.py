"""Tests for MCP server_runner entry point (main and __main__)."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from asap.mcp.server_runner import main

# Start-up (interpreter and imports) took 0.25 s idle and about 3 s with eight busy
# processes per core; an answer from a started server took under 5 ms either way.
PROCESS_TIMEOUT = 30.0
ANSWER_TIMEOUT = 2.0


@asynccontextmanager
async def started_server_runner() -> AsyncIterator[asyncio.subprocess.Process]:
    """Start ``python -m asap.mcp.server_runner`` and wait for its answer to a ping.

    Start-up gets ``PROCESS_TIMEOUT``, as it grows with machine load. A request
    sent after this keeps ``ANSWER_TIMEOUT``, so a server that stops answering
    still fails fast. A process still running on exit is killed.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "asap.mcp.server_runner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(b'{"jsonrpc":"2.0","id":0,"method":"ping"}\n')
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=PROCESS_TIMEOUT)
        assert json.loads(line) == {"jsonrpc": "2.0", "id": 0, "result": {}}
        yield proc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def test_server_runner_main_importable() -> None:
    """main is callable and imports server components."""
    assert callable(main)


def test_server_runner_main_calls_asyncio_run_and_exits_zero() -> None:
    """main() calls asyncio.run(server.run_stdio()) and then sys.exit(0)."""
    with (
        patch("asap.mcp.server_runner.asyncio.run") as mock_run,
        patch("asap.mcp.server_runner.sys.exit") as mock_exit,
    ):
        main()
        mock_run.assert_called_once()
        (coro,) = mock_run.call_args[0]
        assert asyncio.iscoroutine(coro) or callable(getattr(coro, "send", None))
        mock_exit.assert_called_once_with(0)


def test_server_runner_main_broken_pipe_exit_zero() -> None:
    """main() catches BrokenPipeError from asyncio.run and calls sys.exit(0)."""
    with (
        patch("asap.mcp.server_runner.asyncio.run", side_effect=BrokenPipeError()),
        patch("asap.mcp.server_runner.sys.exit") as mock_exit,
    ):
        main()
        mock_exit.assert_called_once_with(0)


def test_server_runner_main_keyboard_interrupt_exit_zero() -> None:
    """main() catches KeyboardInterrupt from asyncio.run and calls sys.exit(0)."""
    with (
        patch("asap.mcp.server_runner.asyncio.run", side_effect=KeyboardInterrupt()),
        patch("asap.mcp.server_runner.sys.exit") as mock_exit,
    ):
        main()
        mock_exit.assert_called_once_with(0)


def test_server_runner_main_registers_echo_tool() -> None:
    """main() builds MCPServer and registers the echo tool."""
    real_run = asyncio.run

    def _run_coro(coro: object) -> None:
        if asyncio.iscoroutine(coro):
            real_run(coro)

    with (
        patch(
            "asap.mcp.server_runner.asyncio.run",
            side_effect=_run_coro,
        ),
        patch("asap.mcp.server_runner.sys.exit"),
        patch("asap.mcp.server.MCPServer") as mock_server_class,
    ):
        mock_server = mock_server_class.return_value
        mock_server.run_stdio = AsyncMock(return_value=None)
        main()
        mock_server_class.assert_called_once()
        call_kw = mock_server_class.call_args[1]
        assert call_kw.get("name") == "asap-mcp-demo"
        mock_server.register_tool.assert_called()
        (name, *_) = mock_server.register_tool.call_args[0]
        assert name == "echo"


@pytest.mark.asyncio
async def test_server_runner_main_runs_until_stdin_closed() -> None:
    """main() runs server run_stdio; with empty stdin it exits without error."""
    # Run server with stdin closed so it exits immediately
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "asap.mcp.server_runner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    proc.stdin.close()
    await proc.wait()
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_server_runner_echo_tool_responds() -> None:
    """Server started via -m asap.mcp.server_runner responds to initialize and echo."""
    init_req = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            }
        )
        + "\n"
    )
    async with started_server_runner() as proc:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(init_req.encode("utf-8"))
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=ANSWER_TIMEOUT)
        data = json.loads(line.decode("utf-8").strip())
        assert "result" in data
        assert data["result"].get("serverInfo", {}).get("name") == "asap-mcp-demo"
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=PROCESS_TIMEOUT)
