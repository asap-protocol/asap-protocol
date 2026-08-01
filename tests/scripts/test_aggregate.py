"""Tests for scripts/telemetry/aggregate.py."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts.telemetry import aggregate as aggregate_mod


def _fake_public_getaddrinfo(
    host: str,
    port: object,
    family: int = 0,
    sock_type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    _ = (host, port, family, sock_type, proto, flags)
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _patch_safe_url_dns() -> Iterator[None]:
    """Keep URL-safety tests deterministic without real DNS lookups."""
    with patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo):
        yield


def test_validate_snapshot_accepts_minimal_shape() -> None:
    sample: dict[str, Any] = {
        "snapshot_version": 1,
        "collected_at": "2026-05-16T12:00:00+00:00",
        "npm": {"@asap-protocol/client": 10},
        "pypi": {"packages": {}, "source": "stub"},
        "github": {
            "source": "github_rest_api",
            "adapter_requests": {"by_framework": {}, "open_count": 0},
        },
        "registry": {"agent_count": 1},
        "site": {"ctr_per_cta": {}},
        "adapter_requests": {},
    }
    aggregate_mod.validate_snapshot(sample)


def test_aggregate_cli_defaults_use_org_cutover_urls() -> None:
    """Aggregate CLI defaults must stay on asap-protocol org after the GitHub transfer."""
    assert aggregate_mod.GITHUB_DEFAULT_OWNER == "asap-protocol"
    assert aggregate_mod.GITHUB_DEFAULT_REPO == "asap-protocol"
    assert aggregate_mod.DEFAULT_REGISTRY_URL == (
        "https://raw.githubusercontent.com/asap-protocol/asap-protocol/main/registry.json"
    )
    assert "adriannoes" not in aggregate_mod.DEFAULT_REGISTRY_URL


def test_flatten_adapter_request_counts() -> None:
    gh: dict[str, Any] = {
        "adapter_requests": {
            "by_framework": {"mastra": 2, "langgraph": 1},
            "unparsed_open_count": 1,
        },
    }
    out = aggregate_mod.flatten_adapter_request_counts(gh)
    assert out["mastra"] == 2
    assert out["_unparsed"] == 1


def test_flatten_adapter_request_counts_skips_non_positive_unparsed() -> None:
    gh: dict[str, Any] = {
        "adapter_requests": {
            "by_framework": {"acme": 1},
            "unparsed_open_count": 0,
        },
    }
    out = aggregate_mod.flatten_adapter_request_counts(gh)
    assert out == {"acme": 1}
    assert "_unparsed" not in out


def test_flatten_adapter_request_counts_handles_missing_or_invalid_block() -> None:
    assert aggregate_mod.flatten_adapter_request_counts({}) == {}
    assert aggregate_mod.flatten_adapter_request_counts({"adapter_requests": "nope"}) == {}
    gh: dict[str, Any] = {
        "adapter_requests": {
            "by_framework": "not-a-dict",
            "unparsed_open_count": 3,
        },
    }
    assert aggregate_mod.flatten_adapter_request_counts(gh) == {"_unparsed": 3}


def test_build_npm_summary_filters_invalid_rows() -> None:
    report: dict[str, Any] = {
        "packages": {
            "@a/good": {"downloads": 10},
            "@b/bad-body": "x",
            "@c/no-dl": {},
            "not-dict": 1,
        }
    }
    assert aggregate_mod.build_npm_summary(report) == {"@a/good": 10}
    assert aggregate_mod.build_npm_summary({}) == {}
    assert aggregate_mod.build_npm_summary({"packages": "no"}) == {}


def test_list_snapshot_files_skips_latest_and_unmatched_names(tmp_path: Path) -> None:
    (tmp_path / "snapshot-2026-05-01.json").write_text("{}", encoding="utf-8")
    (tmp_path / "snapshot-latest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "snapshot-notadate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "snapshot-2026-05-10.json").write_text("{}", encoding="utf-8")
    rows = aggregate_mod.list_snapshot_files(tmp_path)
    dates = [d for d, _ in rows]
    assert dates == [date(2026, 5, 1), date(2026, 5, 10)]
    assert all(p.is_absolute() for _, p in rows)


def test_resolve_previous_snapshot_path() -> None:
    base = Path("/tmp/ignored")
    p1 = base / "a.json"
    p2 = base / "b.json"
    hist = [(date(2026, 5, 1), p1), (date(2026, 5, 8), p2)]
    with patch.object(aggregate_mod, "list_snapshot_files", return_value=hist):
        assert aggregate_mod.resolve_previous_snapshot_path(Path("."), date(2026, 5, 8)) == p1
        assert aggregate_mod.resolve_previous_snapshot_path(Path("."), date(2026, 5, 1)) is None


def test_registry_count_from_snapshot(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"registry": {"agent_count": 7}}),
        encoding="utf-8",
    )
    assert aggregate_mod.registry_count_from_snapshot(good) == 7
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    assert aggregate_mod.registry_count_from_snapshot(bad_json) is None
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"registry": []}), encoding="utf-8")
    assert aggregate_mod.registry_count_from_snapshot(odd) is None


def test_fetch_site_ctr_success_and_degraded_payloads() -> None:
    ok = httpx.Response(
        200,
        json={"site": {"ctr_per_cta": {"x": {"clicks": 1, "views": 10}}}},
        request=httpx.Request("GET", "https://example.com/api/telemetry"),
    )
    mock_ok = MagicMock()
    mock_ok.__enter__.return_value.get.return_value = ok
    mock_ok.__exit__.return_value = None
    with patch.object(aggregate_mod.httpx, "Client", return_value=mock_ok):
        got = aggregate_mod.fetch_site_ctr("https://example.com/api/telemetry", "s")
    assert got["ctr_per_cta"]["x"]["clicks"] == 1

    bad_json = httpx.Response(
        200,
        content=b"not-json",
        request=httpx.Request("GET", "https://example.com/api/telemetry"),
    )
    mock_bad = MagicMock()
    mock_bad.__enter__.return_value.get.return_value = bad_json
    mock_bad.__exit__.return_value = None
    with patch.object(aggregate_mod.httpx, "Client", return_value=mock_bad):
        degraded = aggregate_mod.fetch_site_ctr("https://example.com/api/telemetry", "s")
    assert degraded.get("fetch_error") is True

    not_dict = httpx.Response(
        200,
        json=["a"],
        request=httpx.Request("GET", "https://example.com/api/telemetry"),
    )
    mock_nd = MagicMock()
    mock_nd.__enter__.return_value.get.return_value = not_dict
    mock_nd.__exit__.return_value = None
    with patch.object(aggregate_mod.httpx, "Client", return_value=mock_nd):
        degraded2 = aggregate_mod.fetch_site_ctr("https://example.com/api/telemetry", "s")
    assert degraded2.get("fetch_error") is True

    missing_site = httpx.Response(
        200,
        json={"foo": 1},
        request=httpx.Request("GET", "https://example.com/api/telemetry"),
    )
    mock_ms = MagicMock()
    mock_ms.__enter__.return_value.get.return_value = missing_site
    mock_ms.__exit__.return_value = None
    with patch.object(aggregate_mod.httpx, "Client", return_value=mock_ms):
        degraded3 = aggregate_mod.fetch_site_ctr("https://example.com/api/telemetry", "s")
    assert degraded3.get("fetch_error") is True


def test_sum_pypi_last_week_sums_packages_and_skips_empty() -> None:
    """sum_pypi_last_week totals valid package download counts."""
    assert (
        aggregate_mod.sum_pypi_last_week(
            {
                "pypi": {
                    "packages": {
                        "asap-protocol": {"downloads": {"last_week": 7}},
                        "asap-compliance": {"downloads": {"last_week": 3}},
                        "skip": {"downloads": {"last_week": "nope"}},
                    },
                },
            },
        )
        == 10
    )
    assert aggregate_mod.sum_pypi_last_week({}) is None
    assert aggregate_mod.sum_pypi_last_week({"pypi": {"packages": {}}}) is None


def test_sum_npm_weekly_downloads_ignores_non_int() -> None:
    assert (
        aggregate_mod.sum_npm_weekly_downloads(
            {"npm": {"@a": 4, "@b": 6, "@bad": "x"}},
        )
        == 10
    )
    assert aggregate_mod.sum_npm_weekly_downloads({}) == 0


def test_render_dashboard_adapter_section_sorted() -> None:
    snap: dict[str, Any] = {
        "adapter_requests": {"mastra": 2, "x": 5, "_unparsed": 1},
    }
    hist = [(date(2026, 5, 16), Path("snapshot-2026-05-16.json"))]
    md = aggregate_mod.render_dashboard(snap, hist, weeks=12)
    assert "## Adapter requests" in md
    # x has higher count than mastra; _unparsed last
    x_pos = md.index("| `x` |")
    m_pos = md.index("| `mastra` |")
    u_pos = md.index("| `_unparsed` |")
    assert x_pos < m_pos < u_pos


def test_render_dashboard_sums_pypi_last_week_across_packages(tmp_path: Path) -> None:
    """Dashboard PyPI column must sum all packages (parity with npm Σ)."""
    snap_path = tmp_path / "snapshot-2026-05-16.json"
    snap_path.write_text(
        json.dumps(
            {
                "npm": {"@asap-protocol/client": 4, "@asap-protocol/mastra": 6},
                "pypi": {
                    "packages": {
                        "asap-protocol": {
                            "downloads": {"last_day": 1, "last_week": 7, "last_month": 20},
                        },
                        "asap-compliance": {
                            "downloads": {"last_day": 0, "last_week": 3, "last_month": 9},
                        },
                    },
                    "source": "stub",
                },
                "github": {"repo": {"stargazers_count": 42}},
                "registry": {"agent_count": 1},
            },
        ),
        encoding="utf-8",
    )
    md = aggregate_mod.render_dashboard(
        {},
        [(date(2026, 5, 16), snap_path)],
        weeks=12,
    )
    # npm 4+6=10, pypi 7+3=10 (not first-package-only 7)
    assert "| 2026-05-16 | 10 | 10 | 42 | 1 |" in md


def test_main_passes_expanded_package_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate must collect the full npm/PyPI default sets."""
    captured: dict[str, tuple[str, ...]] = {}

    def _capture_npm(packages: tuple[str, ...], **_k: object) -> dict[str, Any]:
        captured["npm"] = packages
        return {
            "packages": {pkg: {"downloads": 1} for pkg in packages},
        }

    def _capture_pypi(packages: tuple[str, ...], **_k: object) -> dict[str, Any]:
        captured["pypi"] = packages
        return {
            "packages": {
                pkg: {
                    "package": pkg,
                    "downloads": {"last_day": 0, "last_week": 1, "last_month": 2},
                }
                for pkg in packages
            },
            "source": "stub",
        }

    monkeypatch.setattr(aggregate_mod, "collect_npm_weekly", _capture_npm)
    monkeypatch.setattr(aggregate_mod, "collect_pypi_recent", _capture_pypi)
    monkeypatch.setattr(
        aggregate_mod,
        "collect_github_or_placeholder",
        lambda *_a, **_k: {
            "source": "github_rest_api",
            "adapter_requests": {"by_framework": {}, "open_count": 0},
            "repo": {"stargazers_count": 0},
        },
    )
    monkeypatch.setattr(
        aggregate_mod,
        "fetch_registry_json",
        lambda _url: {"agents": [{"id": "a"}]},
    )
    monkeypatch.setattr(aggregate_mod, "update_latest_symlink", lambda *_a, **_k: None)

    code = aggregate_mod.main(
        [
            "--output-dir",
            str(tmp_path),
            "--date",
            "2026-05-16",
            "--allow-github-skip",
        ],
    )
    assert code == 0
    assert set(captured["npm"]) >= {
        "@asap-protocol/client",
        "@asap-protocol/mastra",
        "@asap-protocol/openai-agents",
    }
    assert set(captured["pypi"]) >= {"asap-protocol", "asap-compliance"}
    snap = json.loads((tmp_path / "snapshot-2026-05-16.json").read_text(encoding="utf-8"))
    assert "@asap-protocol/mastra" in snap["npm"]
    assert "asap-compliance" in snap["pypi"]["packages"]


def test_main_writes_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        aggregate_mod,
        "collect_npm_weekly",
        lambda *_a, **_k: {
            "packages": {"@asap-protocol/client": {"downloads": 42}},
        },
    )
    monkeypatch.setattr(
        aggregate_mod,
        "collect_pypi_recent",
        lambda *_a, **_k: {"packages": {}, "source": "stub"},
    )
    monkeypatch.setattr(
        aggregate_mod,
        "collect_github_or_placeholder",
        lambda *_a, **_k: {
            "source": "github_rest_api",
            "adapter_requests": {"by_framework": {"acme": 1}, "open_count": 1},
            "repo": {"stargazers_count": 3},
        },
    )

    def _fake_fetch(_url: str) -> dict[str, Any]:
        return {"agents": [{"id": "a"}]}

    monkeypatch.setattr(aggregate_mod, "fetch_registry_json", _fake_fetch)
    monkeypatch.setattr(aggregate_mod, "update_latest_symlink", lambda *_a, **_k: None)

    code = aggregate_mod.main(
        [
            "--output-dir",
            str(tmp_path),
            "--date",
            "2026-05-16",
            "--allow-github-skip",
        ],
    )
    assert code == 0
    written = (tmp_path / "snapshot-2026-05-16.json").read_text(encoding="utf-8")
    assert "adapter_requests" in written
    assert (tmp_path / "dashboard.md").is_file()


def test_validate_site_endpoint_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        aggregate_mod.validate_site_endpoint("http://example.com/api/telemetry")
    with pytest.raises(ValueError, match="host"):
        aggregate_mod.validate_site_endpoint("https://")


def test_validate_site_endpoint_blocks_private_and_metadata_hosts() -> None:
    with pytest.raises(ValueError, match="public HTTPS URL"):
        aggregate_mod.validate_site_endpoint("https://localhost/api/telemetry")
    with pytest.raises(ValueError, match="public HTTPS URL"):
        aggregate_mod.validate_site_endpoint("https://169.254.169.254/latest/meta-data/")


def test_fetch_site_ctr_rejects_redirect() -> None:
    req = httpx.Request("GET", "https://example.com/api/telemetry")
    redirect = httpx.Response(302, headers={"Location": "https://other.example/"}, request=req)
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = redirect
    mock_client.__exit__.return_value = None
    with (
        patch.object(aggregate_mod.httpx, "Client", return_value=mock_client),
        pytest.raises(ValueError, match="redirect"),
    ):
        aggregate_mod.fetch_site_ctr("https://example.com/api/telemetry", "secret")


def test_strict_missing_github_token_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        aggregate_mod,
        "collect_npm_weekly",
        lambda *_a, **_k: {"packages": {"@asap-protocol/client": {"downloads": 1}}},
    )
    monkeypatch.setattr(
        aggregate_mod,
        "collect_pypi_recent",
        lambda *_a, **_k: {"packages": {}, "source": "stub"},
    )
    rc = aggregate_mod.main(["--output-dir", str(tmp_path), "--date", "2026-05-16"])
    assert rc == 1


def test_strict_github_http_error_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_GITHUB_TOKEN", "tok")

    def _stub_npm(*_a: object, **_k: object) -> dict[str, Any]:
        return {"packages": {"@asap-protocol/client": {"downloads": 1}}}

    def _stub_pypi(*_a: object, **_k: object) -> dict[str, Any]:
        return {"packages": {}, "source": "stub"}

    def _boom(
        _owner: str,
        _repo: str,
        *,
        token: str,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        assert token == "tok"
        req = httpx.Request("GET", "https://api.github.com/repos/o/r")
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("Forbidden", request=req, response=resp)

    monkeypatch.setattr(aggregate_mod, "collect_npm_weekly", _stub_npm)
    monkeypatch.setattr(aggregate_mod, "collect_pypi_recent", _stub_pypi)
    monkeypatch.setattr(aggregate_mod, "collect_github_signals", _boom)
    rc = aggregate_mod.main(["--output-dir", str(tmp_path), "--date", "2026-05-16"])
    assert rc == 1


def test_allow_github_skip_survives_github_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEMETRY_GITHUB_TOKEN", "tok")

    monkeypatch.setattr(
        aggregate_mod,
        "collect_npm_weekly",
        lambda *_a, **_k: {"packages": {"@asap-protocol/client": {"downloads": 1}}},
    )
    monkeypatch.setattr(
        aggregate_mod,
        "collect_pypi_recent",
        lambda *_a, **_k: {"packages": {}, "source": "stub"},
    )

    def _boom(
        _owner: str,
        _repo: str,
        *,
        token: str,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        assert token == "tok"
        req = httpx.Request("GET", "https://api.github.com/")
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("Forbidden", request=req, response=resp)

    monkeypatch.setattr(aggregate_mod, "collect_github_signals", _boom)

    def _fake_fetch(_url: str) -> dict[str, Any]:
        return {"agents": [{"id": "a"}]}

    monkeypatch.setattr(aggregate_mod, "fetch_registry_json", _fake_fetch)
    monkeypatch.setattr(aggregate_mod, "update_latest_symlink", lambda *_a, **_k: None)

    rc = aggregate_mod.main(
        [
            "--output-dir",
            str(tmp_path),
            "--date",
            "2026-05-16",
            "--allow-github-skip",
        ],
    )
    assert rc == 0
    snap_path = tmp_path / "snapshot-2026-05-16.json"
    text = snap_path.read_text(encoding="utf-8")
    assert '"skipped": true' in text
    assert "Forbidden" not in text
    assert "https://api.github.com" not in text
    assert "GitHub telemetry failed; see CI logs." in text


def test_render_dashboard_includes_data_quality_warnings() -> None:
    snap: dict[str, Any] = {
        "adapter_requests": {},
        "github": {"skipped": True, "reason": "403 Forbidden"},
        "site": {"ctr_per_cta": {}, "fetch_error": True},
    }
    md = aggregate_mod.render_dashboard(snap, [], weeks=12)
    assert "## Data quality warnings" in md
