"""``asap compliance-check --output`` is ``text`` or ``json`` after normalization.

``yaml`` is already rejected and is not a neighbor of either token. The
acceptance cases stub the harness so a padded format does not open a socket.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from click.testing import Result
from typer.testing import CliRunner

from asap.cli import app
from asap.testing.compliance import CheckResult, ComplianceReport

_OUTPUT_ERROR = "--output must be 'text' or 'json'"
_OUTPUT_NEIGHBORS: tuple[str, ...] = ("tex", "textx", "jsonextra", "js")
_URL = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _stable_typer_rich_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Rich from wrapping the BadParameter text on a narrow console."""
    import typer.rich_utils as rich_utils

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(rich_utils, "MAX_WIDTH", 200)


def _invoke(args: list[str]) -> Result:
    """Invoke ``asap compliance-check`` with ``args``."""
    return CliRunner().invoke(app, ["compliance-check", *args])


def _report() -> ComplianceReport:
    """One passing identity check, enough to tell text from JSON."""
    return ComplianceReport(
        timestamp=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
        categories_run=["identity"],
        checks=[
            CheckResult(
                name="identity_register_endpoint",
                category="identity",
                passed=True,
                message="ok",
            )
        ],
        score=1.0,
        summary="all passed",
    )


def test_compliance_output_is_exact() -> None:
    """Superstrings and substrings of text and json are rejected before IO."""
    for fmt in _OUTPUT_NEIGHBORS:
        result = _invoke(["--url", _URL, "--output", fmt, "--timeout", "0.2"])
        combined = result.stdout + result.stderr
        assert result.exit_code != 0
        assert _OUTPUT_ERROR in combined
        assert "Could not connect" not in combined


def test_compliance_output_strips_and_lowercases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Padding and case still select the text report and the JSON report."""
    calls: list[str] = []

    async def _run(
        base_url: str,
        *,
        request_timeout: float,
        default_headers: dict[str, str] | None = None,
    ) -> ComplianceReport:
        assert request_timeout == 60.0
        assert default_headers is None
        calls.append(base_url)
        return _report()

    monkeypatch.setattr(
        "asap.cli.compliance_check.run_compliance_harness_v2_from_url",
        _run,
    )
    text_result = _invoke(["--url", _URL, "--output", " TEXT "])
    assert text_result.exit_code == 0, text_result.stdout + text_result.stderr
    assert "Compliance Harness v2" in text_result.stdout
    assert "[PASS] identity.identity_register_endpoint" in text_result.stdout

    json_result = _invoke(["--url", _URL, "--output", "JSON"])
    assert json_result.exit_code == 0, json_result.stdout + json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["score"] == 1.0
    assert payload["summary"] == "all passed"
    assert calls == [_URL, _URL]
