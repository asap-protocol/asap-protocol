"""A deeply nested JSON message gets the answer any other malformed JSON gets.

Past the interpreter's C recursion limit (near 10,000 levels on CPython 3.13),
``json.loads`` raises RecursionError, not JSONDecodeError. These parses run
before authentication and caught only JSONDecodeError, so a nested body was a
500, a nested WebSocket frame closed the connection, and a nested MCP stdio
line stopped the server.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from asap.mcp.protocol import PARSE_ERROR as MCP_PARSE_ERROR
from asap.mcp.server import MCPServer
from asap.transport.jsonrpc import PARSE_ERROR
from asap.transport.server import create_app

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_DEPTH = 20_000
_NESTED = "[" * _DEPTH + "]" * _DEPTH


@pytest.fixture
def client(sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None) -> TestClient:
    app = create_app(sample_manifest, rate_limit="999999/minute")
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/asap", "/asap/stream"])
@pytest.mark.parametrize("body", [_NESTED, "{not json"], ids=["nested", "invalid"])
def test_body_is_a_parse_error(client: TestClient, path: str, body: str) -> None:
    r = client.post(path, content=body, headers={"Content-Type": "application/json"})

    assert r.status_code == 200
    assert r.json()["error"]["code"] == PARSE_ERROR


@pytest.mark.parametrize("frame", [_NESTED, "{not json"], ids=["nested", "invalid"])
def test_websocket_frame_is_ignored(client: TestClient, frame: str) -> None:
    with client.websocket_connect("/asap/ws") as ws:
        ws.send_text(frame)
        ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "asap.send", "id": 7}))
        reply = ws.receive_json()

    assert reply["id"] == 7


async def test_mcp_stdio_line_is_a_parse_error() -> None:
    stdin = io.StringIO(_NESTED + "\n" + '{"jsonrpc": "2.0", "id": 2, "method": "ping"}\n')
    stdout = io.StringIO()

    await MCPServer().serve_stdio(stdin=stdin, stdout=stdout)

    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [None, 2]
    assert replies[0]["error"]["code"] == MCP_PARSE_ERROR
