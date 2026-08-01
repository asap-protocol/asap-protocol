"""Unit tests for typescript-consumer smoke URL/redaction helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HELPERS = (
    _REPO_ROOT
    / "examples"
    / "starters"
    / "typescript-consumer"
    / "smoke_helpers.mjs"
)


def _node_eval(expression: str) -> str:
    """Evaluate an ESM expression that imports smoke_helpers and prints JSON."""
    script = f"""
import {{ assertHttpsOrLoopback, redactSecretsForLog }} from {json.dumps(_HELPERS.as_uri())};
const out = {expression};
process.stdout.write(JSON.stringify(out));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node failed ({result.returncode}): stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result.stdout


def _node_throws(expression: str) -> str:
    """Return thrown Error.message for a failing helper call."""
    script = f"""
import {{ assertHttpsOrLoopback, redactSecretsForLog }} from {json.dumps(_HELPERS.as_uri())};
try {{
  {expression};
  process.stdout.write(JSON.stringify({{ ok: true }}));
}} catch (err) {{
  const message = err instanceof Error ? err.message : String(err);
  process.stdout.write(JSON.stringify({{ ok: false, message }}));
}}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False, payload
    return str(payload["message"])


def test_assert_https_or_loopback_rejects_plain_http_remote() -> None:
    message = _node_throws(
        'assertHttpsOrLoopback("http://example.com/asap", "ASAP_PROVIDER_URL")'
    )
    assert "HTTPS" in message
    assert "example.com" in message


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8787/",
        "http://localhost:8787/",
        "http://[::1]:8787/",
        "https://provider.example/asap",
    ],
)
def test_assert_https_or_loopback_allows_loopback_http_and_https(url: str) -> None:
    href = json.loads(_node_eval(f"assertHttpsOrLoopback({json.dumps(url)}, 'label').href"))
    assert isinstance(href, str)
    assert href.startswith("http")


def test_redact_secrets_for_log_masks_bearer_and_jwt() -> None:
    sample = (
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature "
        "and jwt eyJhbGciOiJIUzI1NiJ9.abc.def"
    )
    out = json.loads(_node_eval(f"redactSecretsForLog({json.dumps(sample)})"))
    assert "Bearer [REDACTED]" in out
    assert "[REDACTED_JWT]" in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
