#!/usr/bin/env python3
"""Fetch last-week npm download counts for `@asap-protocol/*` packages via the public API.

Writes a single JSON document (stdout or ``--output``) suitable for weekly aggregation.

API: https://github.com/npm/registry/blob/master/docs/download-counts.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote
from typing import Any

import httpx

NPM_DOWNLOADS_RANGE_BASE = "https://api.npmjs.org/downloads/range"
DEFAULT_PERIOD = "last-week"
# Aggregate all public scoped packages, not only the client.
DEFAULT_PACKAGES: tuple[str, ...] = (
    "@asap-protocol/client",
    "@asap-protocol/mastra",
    "@asap-protocol/mcp-auth",
    "@asap-protocol/openai-agents",
)
REQUEST_TIMEOUT_SECONDS = 30.0


def _downloads_url(package: str, period: str) -> str:
    """Build the npm downloads range URL (scoped names must be percent-encoded)."""
    encoded = quote(package, safe="")
    return f"{NPM_DOWNLOADS_RANGE_BASE}/{period}/{encoded}"


def sum_downloads_from_range_payload(payload: object) -> int:
    """Sum daily ``downloads`` values from an npm ``/downloads/range/`` JSON body.

    Raises:
        ValueError: If the payload is not a well-formed range response.
    """
    if not isinstance(payload, dict):
        msg = "npm API response must be a JSON object"
        raise ValueError(msg)
    if "error" in payload:
        err = payload.get("error")
        msg = f"npm API error: {err!s}"
        raise ValueError(msg)
    raw_rows = payload.get("downloads")
    if not isinstance(raw_rows, list):
        msg = "npm API response missing 'downloads' array"
        raise ValueError(msg)
    total = 0
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        n = row.get("downloads")
        if isinstance(n, int):
            total += n
        elif isinstance(n, str) and n.isdigit():
            total += int(n)
    return total


def fetch_last_week_downloads(
    client: httpx.Client,
    package: str,
    *,
    period: str = DEFAULT_PERIOD,
) -> dict[str, Any]:
    """GET npm range stats for ``package`` and return a small result object.

    A package that npm does not know yet (HTTP 404, for example one listed in
    ``DEFAULT_PACKAGES`` before its first publish) is no data, not zero: its row has
    ``downloads: None`` and ``not_on_registry: True``, and the run goes on. Every
    other HTTP or network error raises, as before.
    """
    url = _downloads_url(package, period)
    response = client.get(url)
    if response.status_code == HTTPStatus.NOT_FOUND:
        print(
            f"npm telemetry: {package} is not on the npm registry (HTTP 404); recorded as no data",
            file=sys.stderr,
        )
        return {"package": package, "period": period, "downloads": None, "not_on_registry": True}
    response.raise_for_status()
    payload: object = response.json()
    count = sum_downloads_from_range_payload(payload)
    return {
        "package": package,
        "period": period,
        "downloads": count,
    }


def collect_npm_weekly(
    packages: tuple[str, ...],
    *,
    period: str = DEFAULT_PERIOD,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Collect last-week download totals for all ``packages``."""
    collected_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    results: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=timeout) as client:
        for pkg in packages:
            results[pkg] = fetch_last_week_downloads(client, pkg, period=period)
    return {
        "source": "npm_downloads_api",
        "period": period,
        "collected_at": collected_at,
        "packages": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect last-week npm download counts for @asap-protocol/* packages."
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help=f'Downloads range period (default: "{DEFAULT_PERIOD}")',
    )
    parser.add_argument(
        "--package",
        action="append",
        dest="packages",
        metavar="NAME",
        help=(
            f"npm package name (repeatable). Defaults to {', '.join(DEFAULT_PACKAGES)} when omitted."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON to this path instead of stdout",
    )
    args = parser.parse_args(argv)
    pkgs: tuple[str, ...] = tuple(args.packages) if args.packages else DEFAULT_PACKAGES

    report = collect_npm_weekly(pkgs, period=args.period)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
