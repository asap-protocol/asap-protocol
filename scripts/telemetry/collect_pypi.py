#!/usr/bin/env python3
"""Fetch PyPI download aggregates for a package via `pypistats` (PyPI Stats public API).

Mirrors ``pypistats recent <package>`` — **last_day**, **last_week**, **last_month** —
and emits JSON for weekly aggregation.

Requires the project optional extra ``telemetry`` (install: ``uv sync --extra telemetry``
or ``uv sync --all-extras``).

PyPI Stats rate-limits shared CI runner IPs (HTTP 429). Each package request retries
429, 5xx, and transport errors with exponential backoff before the error propagates.

API: https://pypistats.org/api
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypistats
import urllib3

# Cover the protocol package and the compliance validator on PyPI.
DEFAULT_PYPI_PACKAGES: tuple[str, ...] = ("asap-protocol", "asap-compliance")
# Backward-compatible alias for callers that still expect a single default name.
DEFAULT_PYPI_PACKAGE = DEFAULT_PYPI_PACKAGES[0]
_RECENT_KEYS = ("last_day", "last_week", "last_month")
# Waits between attempts: 10s, 30s, 90s (about 2 minutes per package at worst).
PYPISTATS_MAX_ATTEMPTS = 4
PYPISTATS_BACKOFF_BASE_SECONDS = 10.0
PYPISTATS_BACKOFF_FACTOR = 3.0
# ``pypistats`` reports HTTP failures only as ``HTTPError("HTTP Error <status> for url: ...")``.
_HTTP_STATUS_RE = re.compile(r"HTTP Error (\d{3})")


def normalize_recent_counts(data: object) -> dict[str, int]:
    """Validate ``pypistats`` /recent ``data`` object and return integer counts.

    Raises:
        ValueError: If the payload is missing required keys or has invalid types.
    """
    if not isinstance(data, dict):
        msg = "pypistats recent 'data' must be a JSON object"
        raise ValueError(msg)
    out: dict[str, int] = {}
    for key in _RECENT_KEYS:
        if key not in data:
            msg = f"pypistats recent data missing key {key!r}"
            raise ValueError(msg)
        val = data[key]
        if isinstance(val, bool) or not isinstance(val, int | float | str):
            msg = f"pypistats recent {key!r} must be numeric"
            raise ValueError(msg)
        if isinstance(val, str):
            stripped = val.replace(",", "").strip()
            if not stripped.isdigit():
                msg = f"pypistats recent {key!r} is not an integer string"
                raise ValueError(msg)
            out[key] = int(stripped)
        else:
            out[key] = int(val)
    return out


def is_retryable_pypistats_error(exc: urllib3.exceptions.HTTPError) -> bool:
    """Return True for HTTP 429, 5xx, or a transport error without an HTTP status."""
    match = _HTTP_STATUS_RE.search(str(exc))
    if match is None:
        return True
    status = int(match.group(1))
    return status == 429 or status >= 500


def _recent_with_retry(package: str, sleep: Callable[[float], None]) -> object:
    """Call ``pypistats.recent`` and retry rate-limit / transient failures."""
    attempt = 1
    while True:
        try:
            return pypistats.recent(package, format="json")
        except urllib3.exceptions.HTTPError as exc:
            if attempt >= PYPISTATS_MAX_ATTEMPTS or not is_retryable_pypistats_error(exc):
                raise
            delay = PYPISTATS_BACKOFF_BASE_SECONDS * PYPISTATS_BACKOFF_FACTOR ** (attempt - 1)
            print(
                f"pypistats {package}: {exc}; retry {attempt + 1}/{PYPISTATS_MAX_ATTEMPTS} "
                f"in {delay:.0f}s",
                file=sys.stderr,
            )
            sleep(delay)
            attempt += 1


def fetch_pypi_recent(
    package: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Call ``pypistats.recent`` and return a minimal report dict for one package."""
    raw = _recent_with_retry(package, sleep)
    if isinstance(raw, str) and not raw.strip().startswith("{"):
        msg = f"pypistats API: {raw}"
        raise ValueError(msg)
    payload: object = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        msg = "pypistats JSON response must be an object"
        raise ValueError(msg)
    name = payload.get("package")
    if name != package:
        msg = f"pypistats package mismatch: expected {package!r}, got {name!r}"
        raise ValueError(msg)
    counts = normalize_recent_counts(payload.get("data"))
    return {
        "package": package,
        "downloads": counts,
    }


def collect_pypi_recent(
    packages: tuple[str, ...],
) -> dict[str, Any]:
    """Collect recent download windows for each PyPI ``packages`` name."""
    collected_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    results: dict[str, dict[str, Any]] = {}
    for pkg in packages:
        results[pkg] = fetch_pypi_recent(pkg)
    return {
        "source": "pypistats_recent",
        "collected_at": collected_at,
        "packages": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect PyPI download aggregates (pypistats recent) for telemetry."
    )
    parser.add_argument(
        "--package",
        action="append",
        dest="packages",
        metavar="NAME",
        help=(
            "PyPI project name (repeatable). Defaults to asap-protocol and "
            "asap-compliance when omitted."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON to this path instead of stdout",
    )
    args = parser.parse_args(argv)
    pkgs: tuple[str, ...] = tuple(args.packages) if args.packages else DEFAULT_PYPI_PACKAGES

    report = collect_pypi_recent(pkgs)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
