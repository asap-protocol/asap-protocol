"""``asap trace --format`` is ``ascii`` or ``json`` after ``strip().lower()``.

``xml`` is already rejected and is not a neighbor of either token.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from asap.cli import app

_FORMAT_ERROR = "Error: --format must be 'ascii' or 'json'"
_FORMAT_NEIGHBORS: tuple[str, ...] = ("jsonextra", "js", "asciix")
_TRACE_ID = "trace-exact"


def _invoke(args: list[str]) -> Result:
    """Invoke ``asap trace`` with ``args``."""
    return CliRunner().invoke(app, ["trace", *args])


def _write_log(tmp_path: Path) -> Path:
    """Write one received/processed pair for ``trace-exact``."""
    lines = (
        '{"event": "asap.request.received", "envelope_id": "e1", '
        f'"trace_id": "{_TRACE_ID}", "sender": "urn:asap:agent:a", '
        '"recipient": "urn:asap:agent:b", "timestamp": "2026-01-31T12:00:00Z"}',
        '{"event": "asap.request.processed", "envelope_id": "e1", '
        f'"trace_id": "{_TRACE_ID}", "duration_ms": 42}}',
    )
    log_path = tmp_path / "asap.log"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def test_trace_format_is_exact() -> None:
    """Superstrings and substrings of ascii and json are rejected."""
    for fmt in _FORMAT_NEIGHBORS:
        result = _invoke([_TRACE_ID, "--format", fmt])
        combined = result.stdout + result.stderr
        assert result.exit_code != 0
        assert _FORMAT_ERROR in combined


def test_trace_format_strips_and_lowercases(tmp_path: Path) -> None:
    """Padding and case still select the json export and the ASCII diagram."""
    log_path = _write_log(tmp_path)
    json_result = _invoke(
        [_TRACE_ID, "--log-file", str(log_path), "--format", " JSON "],
    )
    assert json_result.exit_code == 0, json_result.stdout + json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["trace_id"] == _TRACE_ID
    hops = payload["hops"]
    assert isinstance(hops, list)
    assert hops[0]["duration_ms"] == 42

    ascii_result = _invoke(
        [_TRACE_ID, "--log-file", str(log_path), "--format", "ASCII"],
    )
    assert ascii_result.exit_code == 0, ascii_result.stdout + ascii_result.stderr
    assert "a -> b (42ms)" in ascii_result.stdout
