"""Tests for scripts/telemetry/collect_npm.py."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts.telemetry.collect_npm import (
    DEFAULT_PACKAGES,
    _downloads_url,
    collect_npm_weekly,
    fetch_last_week_downloads,
    main,
    sum_downloads_from_range_payload,
)

NEW_PACKAGE = "@asap-protocol/not-published-yet"


def _npm_client(status: int, body: dict[str, Any]) -> httpx.Client:
    """An httpx client whose every GET answers ``status`` with the JSON ``body``."""
    return httpx.Client(
        transport=httpx.MockTransport(lambda _req: httpx.Response(status, json=body))
    )


REQUIRED_DEFAULT_NPM_PACKAGES: frozenset[str] = frozenset(
    {
        "@asap-protocol/client",
        "@asap-protocol/mastra",
        "@asap-protocol/mcp-auth",
        "@asap-protocol/openai-agents",
    }
)


class TestDefaultPackages:
    def test_defaults_cover_scoped_adoption_packages(self) -> None:
        assert len(DEFAULT_PACKAGES) >= 4
        assert REQUIRED_DEFAULT_NPM_PACKAGES.issubset(DEFAULT_PACKAGES)

    def test_help_names_every_default_package(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        # argparse wraps long lines, also after a hyphen inside a package name.
        help_text = "".join(capsys.readouterr().out.split())
        for package in DEFAULT_PACKAGES:
            assert package in help_text


class TestDownloadsUrl:
    def test_encodes_scoped_package(self) -> None:
        assert _downloads_url("@asap-protocol/client", "last-week") == (
            "https://api.npmjs.org/downloads/range/last-week/%40asap-protocol%2Fclient"
        )


class TestSumDownloadsFromRangePayload:
    def test_sums_daily_rows(self) -> None:
        payload = {
            "downloads": [
                {"day": "2026-05-10", "downloads": 10},
                {"day": "2026-05-11", "downloads": 5},
            ]
        }
        assert sum_downloads_from_range_payload(payload) == 15

    def test_rejects_non_object(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            sum_downloads_from_range_payload([])

    def test_rejects_api_error_field(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            sum_downloads_from_range_payload({"error": "not found"})

    def test_rejects_missing_downloads(self) -> None:
        with pytest.raises(ValueError, match="downloads"):
            sum_downloads_from_range_payload({"start": "x"})


class TestFetchLastWeekDownloads:
    def test_parses_success_body(self) -> None:
        body = {
            "downloads": [
                {"day": "2026-05-10", "downloads": 3},
                {"day": "2026-05-11", "downloads": 7},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        out = fetch_last_week_downloads(mock_client, "@asap-protocol/client")
        assert out["package"] == "@asap-protocol/client"
        assert out["period"] == "last-week"
        assert out["downloads"] == 10
        mock_client.get.assert_called_once()
        called_url = mock_client.get.call_args[0][0]
        assert "%40asap-protocol%2Fclient" in called_url

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_propagates_other_http_errors(self, status: int) -> None:
        with (
            _npm_client(status, {"error": "rate limited"}) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            fetch_last_week_downloads(client, "@asap-protocol/client")

    def test_propagates_network_errors(self) -> None:
        def _refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with (
            httpx.Client(transport=httpx.MockTransport(_refuse)) as client,
            pytest.raises(httpx.ConnectError),
        ):
            fetch_last_week_downloads(client, "@asap-protocol/client")

    def test_package_unknown_to_npm_is_no_data_not_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """npm answers 404 for a package it does not know yet (e.g. listed before its first publish)."""
        with _npm_client(404, {"error": f"package {NEW_PACKAGE} not found"}) as client:
            out = fetch_last_week_downloads(client, NEW_PACKAGE)

        assert out == {
            "package": NEW_PACKAGE,
            "period": "last-week",
            "downloads": None,
            "not_on_registry": True,
        }
        assert f"{NEW_PACKAGE} is not on the npm registry" in capsys.readouterr().err


class TestCollectNpmWeekly:
    @patch("scripts.telemetry.collect_npm.httpx.Client")
    def test_collects_multiple_packages(self, mock_client_cls: MagicMock) -> None:
        def _response_for_url(url: str) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            if "foo" in url:
                mock_resp.json.return_value = {"downloads": [{"day": "2026-05-10", "downloads": 2}]}
            else:
                mock_resp.json.return_value = {
                    "downloads": [{"day": "2026-05-10", "downloads": 40}]
                }
            return mock_resp

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.side_effect = lambda url: _response_for_url(url)
        mock_client_cls.return_value = mock_client

        report = collect_npm_weekly(
            ("@asap-protocol/client", "@asap-protocol/foo"),
            period="last-week",
        )
        assert report["source"] == "npm_downloads_api"
        assert report["period"] == "last-week"
        assert "collected_at" in report
        pkgs = report["packages"]
        assert pkgs["@asap-protocol/client"]["downloads"] == 40
        assert pkgs["@asap-protocol/foo"]["downloads"] == 2

        assert mock_client.get.call_count == 2
        raw = json.dumps(report)
        assert '"@asap-protocol/client"' in raw

    def test_unknown_package_does_not_stop_the_run(self) -> None:
        def _answer(request: httpx.Request) -> httpx.Response:
            if "not-published-yet" in str(request.url):
                return httpx.Response(404, json={"error": f"package {NEW_PACKAGE} not found"})
            return httpx.Response(200, json={"downloads": [{"day": "2026-11-16", "downloads": 9}]})

        # Built before the patch: the patch replaces httpx.Client for the whole module.
        client = httpx.Client(transport=httpx.MockTransport(_answer))
        with patch("scripts.telemetry.collect_npm.httpx.Client", return_value=client):
            report = collect_npm_weekly((NEW_PACKAGE, "@asap-protocol/client"))

        assert report["packages"][NEW_PACKAGE]["downloads"] is None
        assert report["packages"]["@asap-protocol/client"]["downloads"] == 9
