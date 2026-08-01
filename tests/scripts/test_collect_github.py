"""Tests for scripts/telemetry/collect_github.py."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scripts.telemetry.collect_github import (
    DEFAULT_OWNER,
    DEFAULT_REPO,
    collect_github_signals,
    fetch_popular_referrers,
    fetch_repo_summary,
    fetch_traffic_clones,
    main,
    parse_framework_from_issue_body,
    slug_framework_label,
    summarize_adapter_requests,
)


class TestGithubOrgDefaults:
    def test_defaults_point_at_asap_protocol_org(self) -> None:
        """Org cutover: weekly collectors must not silently query the old personal fork."""
        assert DEFAULT_OWNER == "asap-protocol"
        assert DEFAULT_REPO == "asap-protocol"
        assert "adriannoes" not in DEFAULT_OWNER
        assert "adriannoes" not in DEFAULT_REPO


def _json_response(data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=data,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


class TestFetchRepoSummary:
    def test_parses_counts(self) -> None:
        payload = {
            "stargazers_count": 10,
            "forks_count": 3,
            "open_issues_count": 2,
            "watchers_count": 10,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/o/r"
            return _json_response(payload)

        transport = httpx.MockTransport(handler)
        with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
            out = fetch_repo_summary(client, "o", "r")
        assert out == payload

    def test_raises_on_invalid_shape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"stargazers_count": "x"})

        transport = httpx.MockTransport(handler)
        with (
            httpx.Client(base_url="https://api.github.com", transport=transport) as client,
            pytest.raises(ValueError, match="stargazers_count"),
        ):
            fetch_repo_summary(client, "o", "r")


class TestFetchTrafficClones:
    def test_parses_clone_payload(self) -> None:
        payload = {
            "count": 100,
            "uniques": 40,
            "clones": [{"timestamp": "2026-05-10T00:00:00Z", "count": 5, "uniques": 3}],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/o/r/traffic/clones"
            return _json_response(payload)

        transport = httpx.MockTransport(handler)
        with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
            out = fetch_traffic_clones(client, "o", "r")
        assert out["count"] == 100
        assert out["uniques"] == 40
        assert len(out["clones"]) == 1


class TestFetchPopularReferrers:
    def test_filters_well_formed_rows(self) -> None:
        payload: list[dict[str, Any]] = [
            {"referrer": "Google", "views": 9, "uniques": 4},
            {"bad": True},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/o/r/traffic/popular/referrers"
            return _json_response(payload)

        transport = httpx.MockTransport(handler)
        with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
            out = fetch_popular_referrers(client, "o", "r")
        assert out == [{"referrer": "Google", "views": 9, "uniques": 4}]


class TestCollectGithubSignals:
    def test_joins_all_endpoints(self) -> None:
        repo_payload = {
            "stargazers_count": 1,
            "forks_count": 2,
            "open_issues_count": 3,
            "watchers_count": 1,
        }
        clone_payload = {"count": 7, "uniques": 5, "clones": []}
        ref_payload = [{"referrer": "X", "views": 1, "uniques": 1}]
        issues_payload: list[dict[str, Any]] = [
            {
                "number": 1,
                "title": "Add Mastra",
                "body": "### Framework name\n\nMastra\n",
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/repos/acme/widget":
                return _json_response(repo_payload)
            if path == "/repos/acme/widget/traffic/clones":
                return _json_response(clone_payload)
            if path == "/repos/acme/widget/traffic/popular/referrers":
                return _json_response(ref_payload)
            if path == "/repos/acme/widget/issues":
                return _json_response(issues_payload)
            return _json_response({"message": "unexpected"}, status=404)

        transport = httpx.MockTransport(handler)
        with httpx.Client(base_url="https://api.github.com", transport=transport) as client:
            report = collect_github_signals("acme", "widget", token="", client=client)
        assert report["source"] == "github_rest_api"
        assert report["repository"] == "acme/widget"
        assert report["repo"] == repo_payload
        assert report["traffic_clones"]["count"] == 7
        assert report["traffic_referrers"] == ref_payload
        assert report["adapter_requests"]["by_framework"]["mastra"] == 1


class TestAdapterRequestParsing:
    def test_parse_framework_heading(self) -> None:
        body = "### Framework name\n\nOpenAI Agents SDK\n\n### Use case\n\ndemo"
        assert parse_framework_from_issue_body(body) == "OpenAI Agents SDK"

    def test_parse_framework_line(self) -> None:
        body = "Some intro\n\nFramework: LangGraph\n"
        assert parse_framework_from_issue_body(body) == "LangGraph"

    def test_slug_framework_label(self) -> None:
        assert slug_framework_label("OpenAI Agents SDK") == "openai-agents-sdk"

    def test_summarize_groups_and_unparsed(self) -> None:
        issues: list[dict[str, Any]] = [
            {"body": "### Framework\n\nMastra"},
            {"body": "no framework here"},
            {"body": "Framework: mastra"},
        ]
        summary = summarize_adapter_requests(issues)
        assert summary["open_count"] == 3
        assert summary["by_framework"]["mastra"] == 2
        assert summary["unparsed_open_count"] == 1


class TestCollectGithubMain:
    def test_main_rejects_missing_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert main(["--owner", "a", "--repo", "b"]) == 1
