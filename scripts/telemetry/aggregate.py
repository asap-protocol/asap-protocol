#!/usr/bin/env python3
"""Join weekly telemetry collectors into a snapshot JSON + markdown dashboard.

Writes:

- ``private/telemetry/snapshot-YYYY-MM-DD.json`` — validated against an embedded JSON Schema.
- ``private/telemetry/dashboard.md`` — ~12 week trend table + adapter request section.
- ``private/telemetry/snapshot-latest.json`` — symlink to the dated snapshot (Unix CI).

Dashboard trend rows:
    When fewer than two historical snapshots exist under ``--output-dir``, the table
    documents **week-over-week growth** expectations but only includes the current row;
    re-run weekly aggregation to accumulate history locally or download CI artifacts.

Operator hints for **site / CTR**:
    Vercel Web Analytics aggregates are not available via a public REST API. The
    snapshot's ``site.ctr_per_cta`` is filled from ``--site-endpoint`` (GET with
    ``Authorization: Bearer <TELEMETRY_TOKEN>``) when passed, else placeholders.

    NPM/PyPI collectors run **serially** in this script so weekly CI stays friendly
    to upstream rate limits as additional packages are added.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

import httpx
import urllib3
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from scripts.telemetry.collect_github import (
    DEFAULT_OWNER as GITHUB_DEFAULT_OWNER,
    DEFAULT_REPO as GITHUB_DEFAULT_REPO,
    collect_github_signals,
)
from scripts.telemetry.collect_npm import DEFAULT_PACKAGES, collect_npm_weekly
from scripts.telemetry.collect_pypi import DEFAULT_PYPI_PACKAGES, collect_pypi_recent
from scripts.telemetry.collect_registry import DEFAULT_REGISTRY_URL, collect_registry_snapshot
from scripts.telemetry.collect_registry import fetch_registry_json
from scripts.lib.safe_url import is_safe_http_url


class NpmPackageRow(TypedDict, total=False):
    """One package entry from ``collect_npm_weekly``."""

    downloads: int
    package: str
    period: str


class NpmWeeklyIngress(TypedDict, total=False):
    """npm collector JSON consumed by ``build_npm_summary``."""

    packages: dict[str, NpmPackageRow]


class PyPiDownloadsIngress(TypedDict):
    """Recent download windows from ``pypistats``."""

    last_day: int
    last_week: int
    last_month: int


class PyPiPackageIngress(TypedDict):
    package: str
    downloads: PyPiDownloadsIngress


class PyPiWeeklyIngress(TypedDict, total=False):
    """PyPI collector JSON (or fetch-error placeholder) stored under snapshot ``pypi``."""

    source: str
    collected_at: str
    packages: dict[str, PyPiPackageIngress]
    fetch_error: bool
    reason: str


class AdapterRequestsIngress(TypedDict, total=False):
    """GitHub collector ``adapter_requests`` block."""

    label: str
    open_count: int
    by_framework: dict[str, int]
    unparsed_open_count: int


class GitHubTelemetryIngress(TypedDict, total=False):
    """GitHub collector or placeholder payload stored under snapshot ``github``."""

    source: str
    repository: str
    collected_at: str
    skipped: bool
    reason: str
    repo: dict[str, int]
    adapter_requests: AdapterRequestsIngress


class RegistryTelemetryIngress(TypedDict, total=False):
    """Registry snapshot block written into the weekly snapshot."""

    source: str
    registry_ref: str
    format: str
    collected_at: str
    agent_count: int
    previous_agent_count: int | None
    growth: int | None


class SiteCtrIngress(TypedDict, total=False):
    """Site CTR payload from ``/api/telemetry`` or skipped/error placeholders."""

    ctr_per_cta: dict[str, Any]
    fetch_error: bool
    note: str
    skipped: bool
    reason: str


# ruff: noqa: E501 — valid JSON Schema string
SNAPSHOT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://asap-protocol.com/schemas/telemetry-snapshot-v1.json",
    "title": "ASAP telemetry weekly snapshot",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "snapshot_version",
        "collected_at",
        "npm",
        "pypi",
        "github",
        "registry",
        "site",
        "adapter_requests",
    ],
    "properties": {
        "snapshot_version": {"type": "integer", "const": 1},
        "collected_at": {"type": "string", "minLength": 10},
        "npm": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        },
        "pypi": {
            "type": "object",
            "required": ["packages"],
            "properties": {
                "packages": {"type": "object"},
                "fetch_error": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        "github": {
            "type": "object",
            "required": ["adapter_requests"],
            "properties": {
                "adapter_requests": {
                    "type": "object",
                    "required": ["by_framework"],
                    "properties": {
                        "by_framework": {
                            "type": "object",
                            "additionalProperties": {"type": "integer"},
                        },
                    },
                },
            },
        },
        "registry": {
            "type": "object",
            "required": ["agent_count"],
            "properties": {"agent_count": {"type": "integer"}},
        },
        "site": {
            "type": "object",
            "required": ["ctr_per_cta"],
            "properties": {
                "ctr_per_cta": {"type": "object"},
                "fetch_error": {"type": "boolean"},
                "note": {"type": "string"},
                "skipped": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        "adapter_requests": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        },
    },
}

_REQUEST_TIMEOUT = 45.0
_SNAPSHOT_NAME_RE = re.compile(r"^snapshot-(\d{4}-\d{2}-\d{2})\.json$")


def validate_snapshot(instance: dict[str, Any]) -> None:
    """Validate ``instance`` against :data:`SNAPSHOT_SCHEMA`."""
    validator = Draft202012Validator(SNAPSHOT_SCHEMA)
    validator.validate(instance)


def list_snapshot_files(output_dir: Path) -> list[tuple[date, Path]]:
    """Return dated snapshot files under ``output_dir`` (excludes ``snapshot-latest``)."""
    rows: list[tuple[date, Path]] = []
    for path in output_dir.glob("snapshot-*.json"):
        if path.name == "snapshot-latest.json":
            continue
        match = _SNAPSHOT_NAME_RE.match(path.name)
        if not match:
            continue
        rows.append((date.fromisoformat(match.group(1)), path.resolve()))
    rows.sort(key=lambda x: x[0])
    return rows


def resolve_previous_snapshot_path(output_dir: Path, current: date) -> Path | None:
    """Pick the latest snapshot strictly before ``current``."""
    candidates = [p for d, p in list_snapshot_files(output_dir) if d < current]
    if not candidates:
        return None
    return candidates[-1]


def registry_count_from_snapshot(snapshot_path: Path) -> int | None:
    """Read ``registry.agent_count`` from an existing snapshot, if present."""
    try:
        raw: object = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    reg = raw.get("registry")
    if not isinstance(reg, dict):
        return None
    count = reg.get("agent_count")
    return count if isinstance(count, int) else None


def build_npm_summary(report: Mapping[str, object]) -> dict[str, int]:
    """Flatten npm collector output to ``{package: downloads}``."""
    pkgs = report.get("packages")
    if not isinstance(pkgs, dict):
        return {}
    out: dict[str, int] = {}
    for name, body in pkgs.items():
        if not isinstance(body, dict):
            continue
        dl = body.get("downloads")
        if isinstance(dl, int):
            out[str(name)] = dl
    return out


def flatten_adapter_request_counts(github_report: Mapping[str, object]) -> dict[str, int]:
    """Build snapshot ``adapter_requests`` counts from GitHub collector output."""
    block = github_report.get("adapter_requests")
    if not isinstance(block, dict):
        return {}
    by_fw = block.get("by_framework")
    out: dict[str, int] = {}
    if isinstance(by_fw, dict):
        for k, v in by_fw.items():
            if isinstance(k, str) and isinstance(v, int):
                out[k] = v
    unparsed = block.get("unparsed_open_count")
    if isinstance(unparsed, int) and unparsed > 0:
        out["_unparsed"] = unparsed
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def validate_site_endpoint(endpoint: str) -> None:
    """Require a public HTTPS URL before attaching a Bearer secret."""
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        msg = "TELEMETRY_SITE_ENDPOINT must use https://"
        raise ValueError(msg)
    if not parsed.netloc:
        msg = "TELEMETRY_SITE_ENDPOINT must include a host"
        raise ValueError(msg)
    if not is_safe_http_url(endpoint):
        msg = "TELEMETRY_SITE_ENDPOINT must be a public HTTPS URL"
        raise ValueError(msg)


def fetch_site_ctr(
    endpoint: str,
    bearer: str,
    *,
    timeout: float = _REQUEST_TIMEOUT,
) -> SiteCtrIngress:
    """GET a protected telemetry route and return its JSON ``site`` object or empty."""
    validate_site_endpoint(endpoint)
    headers = {"Authorization": f"Bearer {bearer}"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(endpoint, headers=headers)
            if response.is_redirect:
                msg = "TELEMETRY_SITE_ENDPOINT must not redirect when sending Authorization"
                raise ValueError(msg)
            response.raise_for_status()
            payload: object = response.json()
    except json.JSONDecodeError:
        return {"ctr_per_cta": {}, "fetch_error": True}
    except ValueError:
        raise
    except httpx.HTTPError:
        return {"ctr_per_cta": {}, "fetch_error": True}
    if not isinstance(payload, dict):
        return {"ctr_per_cta": {}, "fetch_error": True}
    site = payload.get("site")
    if isinstance(site, dict) and isinstance(site.get("ctr_per_cta"), dict):
        return cast(SiteCtrIngress, site)
    return {"ctr_per_cta": {}, "fetch_error": True}


def collect_github_or_placeholder(
    owner: str,
    repo: str,
    token: str,
) -> GitHubTelemetryIngress:
    """Run GitHub collector or return a non-fatal placeholder (``--allow-github-skip`` only)."""
    if not token:
        return cast(
            GitHubTelemetryIngress,
            {
                "source": "github_rest_api",
                "skipped": True,
                "reason": "TELEMETRY_GITHUB_TOKEN / GITHUB_TOKEN not set",
                "repository": f"{owner}/{repo}",
                "adapter_requests": {
                    "label": "adapter-request",
                    "open_count": 0,
                    "by_framework": {},
                },
            },
        )
    try:
        return cast(GitHubTelemetryIngress, collect_github_signals(owner, repo, token=token))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"GitHub telemetry skipped: {exc}", file=sys.stderr)
        return cast(
            GitHubTelemetryIngress,
            {
                "source": "github_rest_api",
                "skipped": True,
                "reason": "GitHub telemetry failed; see CI logs.",
                "repository": f"{owner}/{repo}",
                "adapter_requests": {
                    "label": "adapter-request",
                    "open_count": 0,
                    "by_framework": {},
                },
            },
        )


def collect_pypi_or_placeholder(packages: tuple[str, ...]) -> PyPiWeeklyIngress:
    """Run the PyPI collector; after its retries, record a fetch error instead of failing."""
    try:
        return cast(PyPiWeeklyIngress, collect_pypi_recent(packages))
    except (urllib3.exceptions.HTTPError, ValueError) as exc:
        print(f"PyPI telemetry degraded: {exc}", file=sys.stderr)
        return {
            "source": "pypistats_recent",
            "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "packages": {},
            "fetch_error": True,
            "reason": "PyPI Stats fetch failed; see CI logs.",
        }


def sum_npm_weekly_downloads(raw_hist: Mapping[str, Any]) -> int:
    """Sum integer weekly download counts from a snapshot ``npm`` map.

    Example:
        >>> sum_npm_weekly_downloads({"npm": {"@asap-protocol/client": 4}})
        4
    """
    npm_vals = raw_hist.get("npm")
    total = 0
    if isinstance(npm_vals, dict):
        for v in npm_vals.values():
            if isinstance(v, int):
                total += v
    return total


def sum_pypi_last_week(raw_hist: Mapping[str, Any]) -> int | None:
    """Sum ``downloads.last_week`` across PyPI packages in a snapshot row.

    Returns ``None`` when no integer ``last_week`` values are present (dashboard
    renders an em dash). This helper keeps ``render_dashboard`` free of inline
    package-sum loops.

    Example:
        >>> sum_pypi_last_week({"pypi": {"packages": {"asap-protocol": {"downloads": {"last_week": 7}}}}})
        7
    """
    pypi_obj = raw_hist.get("pypi")
    if not isinstance(pypi_obj, dict):
        return None
    pkgs = pypi_obj.get("packages")
    if not isinstance(pkgs, dict):
        return None
    total = 0
    found = False
    for pkg_val in pkgs.values():
        if not isinstance(pkg_val, dict):
            continue
        dl = pkg_val.get("downloads")
        if not isinstance(dl, dict):
            continue
        lw = dl.get("last_week")
        if isinstance(lw, int):
            total += lw
            found = True
    return total if found else None


def render_dashboard(
    snapshot: dict[str, Any],
    history_paths: list[tuple[date, Path]],
    *,
    weeks: int = 12,
) -> str:
    """Render markdown for ``dashboard.md``."""
    lines: list[str] = [
        "# ASAP adoption telemetry dashboard",
        "",
        "_Generated by `scripts/telemetry/aggregate.py`. Do not edit by hand._",
        "",
        "## 12-week trend (public-source signals)",
        "",
        "| Week ending (UTC) | npm Σ weekly DL | PyPI `last_week` | GitHub stars | Registry agents |",
        "|------------------|-----------------|------------------|--------------|-----------------|",
    ]

    selected = history_paths[-weeks:]
    if not selected:
        lines.append(
            "| _(no snapshots yet)_ | — | — | — | — |",
        )
    for snap_date, path in selected:
        try:
            raw_hist: object = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw_hist, dict):
            continue
        npm_sum = sum_npm_weekly_downloads(raw_hist)
        pypi_total = sum_pypi_last_week(raw_hist)
        pypi_week: str | int = pypi_total if pypi_total is not None else "—"
        gh_stars: str | int = "—"
        gh = raw_hist.get("github")
        if isinstance(gh, dict):
            repo_info = gh.get("repo")
            if isinstance(repo_info, dict):
                sc = repo_info.get("stargazers_count")
                if isinstance(sc, int):
                    gh_stars = sc
        reg_c: str | int = "—"
        reg = raw_hist.get("registry")
        if isinstance(reg, dict):
            ac = reg.get("agent_count")
            if isinstance(ac, int):
                reg_c = ac
        lines.append(
            f"| {snap_date.isoformat()} | {npm_sum} | {pypi_week} | {gh_stars} | {reg_c} |",
        )

    if len(selected) < 2:
        lines.extend(
            [
                "",
                "> **Growth**: With only one snapshot on disk, week-over-week deltas are omitted. "
                "Keep weekly artifacts (or run locally into `private/telemetry/`) to populate this table.",
            ],
        )

    ad_lines = ["", "## Adapter requests (open issues, by framework)", ""]
    adapter_counts = snapshot.get("adapter_requests")
    if not isinstance(adapter_counts, dict):
        adapter_counts = {}
    # Sort by count desc; keep `_unparsed` last for readability.
    items = [(k, v) for k, v in adapter_counts.items() if isinstance(v, int) and v > 0]
    items.sort(key=lambda kv: (kv[0] == "_unparsed", -kv[1], kv[0]))
    if not items:
        ad_lines.append("_No open adapter-request issues counted in this snapshot._")
    else:
        ad_lines.append("| Framework (slug) | Open requests |")
        ad_lines.append("|------------------|---------------|")
        for name, count in items:
            ad_lines.append(f"| `{name}` | {count} |")
    lines.extend(ad_lines)

    degraded: list[str] = []
    gh_obj = snapshot.get("github")
    if isinstance(gh_obj, dict) and gh_obj.get("skipped"):
        reason = gh_obj.get("reason", "unknown")
        degraded.append(
            f"GitHub telemetry skipped ({reason}); adapter-request counts may be zeroed."
        )
    pypi_obj = snapshot.get("pypi")
    if isinstance(pypi_obj, dict) and pypi_obj.get("fetch_error"):
        reason = pypi_obj.get("reason", "unknown")
        degraded.append(f"PyPI telemetry missing ({reason}); the PyPI column shows — this week.")
    site_obj = snapshot.get("site")
    if isinstance(site_obj, dict) and site_obj.get("fetch_error"):
        degraded.append("Site CTR fetch failed; see `site.fetch_error` in the snapshot JSON.")
    if degraded:
        lines.extend(["", "## Data quality warnings", ""])
        lines.extend(f"- {note}" for note in degraded)

    lines.append("")
    return "\n".join(lines)


def update_latest_symlink(output_dir: Path, snapshot_filename: str) -> None:
    """Point ``snapshot-latest.json`` at ``snapshot_filename`` (relative)."""
    link = output_dir / "snapshot-latest.json"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(snapshot_filename, target_is_directory=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate weekly telemetry into a snapshot + dashboard."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("private/telemetry"),
        help="Directory for snapshot + dashboard (created if missing).",
    )
    parser.add_argument(
        "--date",
        default="",
        help="UTC date YYYY-MM-DD for filename (default: today UTC).",
    )
    parser.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help="HTTPS URL for registry.json mirror.",
    )
    parser.add_argument(
        "--github-owner",
        default=GITHUB_DEFAULT_OWNER,
        help="GitHub owner for collectors.",
    )
    parser.add_argument(
        "--github-repo",
        default=GITHUB_DEFAULT_REPO,
        help="GitHub repo for collectors.",
    )
    parser.add_argument(
        "--site-endpoint",
        default=os.environ.get("TELEMETRY_SITE_ENDPOINT", "").strip(),
        help="Full URL to GET /api/telemetry (Bearer TELEMETRY_TOKEN).",
    )
    parser.add_argument(
        "--telemetry-token-env",
        default="TELEMETRY_TOKEN",
        help="Env var for Bearer token when fetching --site-endpoint.",
    )
    parser.add_argument(
        "--allow-github-skip",
        action="store_true",
        help=(
            "Allow missing or failing GitHub REST telemetry (placeholder snapshot data). "
            "Do not use for CI promotion gates."
        ),
    )
    args = parser.parse_args(argv)

    allow_github_skip: bool = args.allow_github_skip
    gh_token = os.environ.get("TELEMETRY_GITHUB_TOKEN", "").strip()
    if allow_github_skip:
        gh_token = gh_token or os.environ.get("GITHUB_TOKEN", "").strip()
    elif not gh_token:
        # Fail before any collector runs so a misconfigured job spends no PyPI Stats quota.
        print(
            "Missing TELEMETRY_GITHUB_TOKEN; GitHub telemetry is required "
            "(pass --allow-github-skip for local placeholder runs only).",
            file=sys.stderr,
        )
        return 1

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    snap_day = date.fromisoformat(args.date) if args.date else datetime.now(UTC).date()
    snap_label = snap_day.isoformat()

    prev_path = resolve_previous_snapshot_path(output_dir, snap_day)
    previous_registry_count = registry_count_from_snapshot(prev_path) if prev_path else None

    npm_report = cast(NpmWeeklyIngress, collect_npm_weekly(DEFAULT_PACKAGES))
    pypi_report = collect_pypi_or_placeholder(DEFAULT_PYPI_PACKAGES)

    github_report: GitHubTelemetryIngress
    if allow_github_skip:
        github_report = collect_github_or_placeholder(
            args.github_owner,
            args.github_repo,
            gh_token,
        )
    else:
        try:
            github_report = cast(
                GitHubTelemetryIngress,
                collect_github_signals(
                    args.github_owner,
                    args.github_repo,
                    token=gh_token,
                ),
            )
        except (httpx.HTTPError, ValueError) as exc:
            print(f"GitHub telemetry failed: {exc}", file=sys.stderr)
            return 1

    try:
        raw_registry = fetch_registry_json(args.registry_url)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        print(f"Registry fetch failed: {exc}", file=sys.stderr)
        return 1

    registry_report = cast(
        RegistryTelemetryIngress,
        collect_registry_snapshot(
            raw_registry,
            registry_ref=args.registry_url,
            previous_count=previous_registry_count,
        ),
    )

    site_details: SiteCtrIngress
    telemetry_secret = os.environ.get(args.telemetry_token_env, "").strip()
    if args.site_endpoint and telemetry_secret:
        try:
            site_details = fetch_site_ctr(args.site_endpoint, telemetry_secret)
        except ValueError as exc:
            print(f"Site telemetry misconfigured: {exc}", file=sys.stderr)
            return 1
    else:
        site_details = {
            "ctr_per_cta": {},
            "note": "Site metrics skipped (set TELEMETRY_SITE_ENDPOINT and TELEMETRY_TOKEN to pull /api/telemetry).",
        }

    snapshot_site: dict[str, Any] = {"ctr_per_cta": site_details.get("ctr_per_cta", {})}
    for copy_key in ("fetch_error", "note", "skipped", "reason"):
        if copy_key in site_details:
            snapshot_site[copy_key] = site_details[copy_key]

    snapshot: dict[str, Any] = {
        "snapshot_version": 1,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "npm": build_npm_summary(npm_report),
        "pypi": pypi_report,
        "github": github_report,
        "registry": registry_report,
        "site": snapshot_site,
        "adapter_requests": flatten_adapter_request_counts(github_report),
    }

    try:
        validate_snapshot(snapshot)
    except ValidationError as exc:
        print(f"Snapshot failed schema validation: {exc}", file=sys.stderr)
        return 1

    snapshot_name = f"snapshot-{snap_label}.json"
    snapshot_path = output_dir / snapshot_name
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    history = list_snapshot_files(output_dir)
    dashboard_text = render_dashboard(snapshot, history, weeks=12)
    (output_dir / "dashboard.md").write_text(dashboard_text, encoding="utf-8")

    try:
        update_latest_symlink(output_dir, snapshot_name)
    except OSError:
        # Windows developer machines may lack symlink privilege; CI is Linux.
        print(
            "Note: could not create snapshot-latest.json symlink (optional).",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
