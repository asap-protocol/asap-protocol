"""Unit tests for Lite Registry bot PR helpers."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from asap.discovery.registry import LiteRegistry, RegistryEntry
from asap.models.enums import VerificationState
from asap.registry import bot_pr
from asap.registry.bot_pr import (
    AUTO_REGISTRATION_PR_LABEL,
    BotPRSettings,
    _default_branch_prep,
    _github_create_pull_request,
    _run_git,
    _sanitize_urn_for_branch,
    conventional_commit_message,
    merge_lite_registry,
    merge_lite_registry_json_text,
    open_registry_pull_request,
)


def test_merge_lite_registry_sorted_deduped() -> None:
    existing = RegistryEntry(
        id="urn:asap:agent:b",
        name="B",
        description="d",
        endpoints={"http": "https://b.example/asap", "manifest": "https://b.example/m"},
        skills=["s"],
        asap_version="2.0.0",
    )
    newer = RegistryEntry(
        id="urn:asap:agent:a",
        name="A",
        description="d",
        endpoints={"http": "https://a.example/asap", "manifest": "https://a.example/m"},
        skills=["s"],
        asap_version="2.0.0",
    )
    lr = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agents=[existing],
    )
    merged = merge_lite_registry(lr, newer)
    ids = [a.id for a in merged.agents]
    assert ids == sorted(ids)
    assert {a.id for a in merged.agents} == {"urn:asap:agent:a", "urn:asap:agent:b"}


def test_merge_lite_registry_json_roundtrip() -> None:
    lr = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agents=[],
    )
    entry = RegistryEntry(
        id="urn:asap:agent:new",
        name="New",
        description="d",
        endpoints={"http": "https://n.example/asap", "manifest": "https://n.example/m"},
        skills=["echo"],
        asap_version="2.2.0",
        verification=None,
    )
    text = lr.model_dump_json()
    out = merge_lite_registry_json_text(text, entry)
    parsed = LiteRegistry.model_validate_json(out)
    assert len(parsed.agents) == 1
    assert parsed.agents[0].id == entry.id


def test_conventional_commit_message_format() -> None:
    entry = RegistryEntry(
        id="urn:asap:agent:demo",
        name="Demo Agent",
        description="d",
        endpoints={"http": "https://x/asap", "manifest": "https://x/m"},
        skills=[],
        asap_version="1.0.0",
    )
    msg = conventional_commit_message(entry)
    assert msg == "feat(registry): auto-register Demo Agent (urn:asap:agent:demo)"


def test_merge_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid LiteRegistry"):
        merge_lite_registry_json_text("{not json", _dummy_entry())


def test_merge_lite_registry_rejects_existing_id() -> None:
    """Duplicate URN must not replace endpoints (auto-registration hijack)."""
    existing = _dummy_entry()
    hijack = RegistryEntry(
        id=existing.id,
        name="Hijack",
        description="attacker listing",
        endpoints={
            "http": "https://evil.example/asap",
            "manifest": "https://evil.example/m",
        },
        skills=["s"],
        asap_version="2.0.0",
    )
    lr = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agents=[existing],
    )
    with pytest.raises(ValueError, match="already registered"):
        merge_lite_registry(lr, hijack)
    assert lr.agents[0].endpoints["http"] == existing.endpoints["http"]


def test_merge_lite_registry_json_text_rejects_existing_id() -> None:
    existing = _dummy_entry()
    lr = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agents=[existing],
    )
    with pytest.raises(ValueError, match="already registered"):
        merge_lite_registry_json_text(lr.model_dump_json(), existing)


def _dummy_entry() -> RegistryEntry:
    return RegistryEntry(
        id="urn:asap:agent:x",
        name="X",
        description="d",
        endpoints={"http": "https://x/asap", "manifest": "https://x/m"},
        skills=[],
        asap_version="1.0.0",
        verification=None,
    )


def test_authenticated_clone_url_inserts_token() -> None:
    s = BotPRSettings(owner="a", repo="b", github_token="ghp_test")
    out = s._authenticated_clone_url("https://github.com/a/b.git")
    assert "x-access-token:ghp_test@" in out


def test_is_reserved_destination() -> None:
    assert bot_pr.is_reserved_destination("https://127.0.0.1/m")
    assert not bot_pr.is_reserved_destination("https://example.com/m")


def test_is_reserved_destination_private_ipv4() -> None:
    assert bot_pr.is_reserved_destination("https://10.0.0.1/path")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://localhost/m", True),
        ("https://[::1]/m", True),
        ("https://0.0.0.0/m", True),
        ("https://169.254.1.1/m", True),
        ("https://172.16.0.1/m", True),
        ("https://192.168.1.1/m", True),
        ("https://[fc00::1]/m", True),
        ("https://[::ffff:127.0.0.1]/m", True),
        ("https://example.com/m", False),
    ],
)
def test_is_reserved_destination_ssrf_matrix(url: str, expected: bool) -> None:
    assert bot_pr.is_reserved_destination(url) is expected


def test_run_git_uses_fixed_argv_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(bot_pr.subprocess, "run", fake_run)
    _run_git(["git", "version"])
    assert captured["argv"] == ["git", "version"]
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 120


def test_run_git_propagates_called_process_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(bot_pr.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        _run_git(["git", "status"])


@pytest.mark.asyncio
async def test_github_create_pull_request_success() -> None:
    requests_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        if request.method == "POST" and "/repos/o/r/pulls" in str(request.url):
            return httpx.Response(
                201,
                json={"html_url": "https://github.com/o/r/pull/42", "number": 42},
            )
        if request.method == "POST" and "/repos/o/r/issues/42/labels" in str(request.url):
            assert AUTO_REGISTRATION_PR_LABEL.encode() in request.content
            return httpx.Response(200, json=[{"name": AUTO_REGISTRATION_PR_LABEL}])
        return httpx.Response(404, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        settings = BotPRSettings(owner="o", repo="r", github_token="tok")
        url = await _github_create_pull_request(
            settings=settings,
            head_branch="auto-reg/x",
            title="t",
            body="b",
            http_client=client,
        )
    assert url == "https://github.com/o/r/pull/42"
    assert any("/issues/42/labels" in r for r in requests_log)


@pytest.mark.asyncio
async def test_github_create_pull_request_label_failure_propagates() -> None:
    """Label POST failure must abort PR flow (auto-merge gating depends on label)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/repos/o/r/pulls" in str(request.url):
            return httpx.Response(
                201,
                json={"html_url": "https://github.com/o/r/pull/7", "number": 7},
            )
        if request.method == "POST" and "/repos/o/r/issues/7/labels" in str(request.url):
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return httpx.Response(404, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        settings = BotPRSettings(owner="o", repo="r", github_token="tok")
        with pytest.raises(httpx.HTTPStatusError):
            await _github_create_pull_request(
                settings=settings,
                head_branch="auto-reg/x",
                title="t",
                body="b",
                http_client=client,
            )


def test_default_branch_prep_missing_registry_raises(tmp_path: Path) -> None:
    """Missing registry.json in worktree must fail before git subprocesses run."""
    settings = BotPRSettings(
        owner="o", repo="r", github_token="tok", registry_path_in_repo="registry.json"
    )
    with pytest.raises(FileNotFoundError, match="Missing"):
        _default_branch_prep(tmp_path, _dummy_entry(), "https://cdn.example/m.json", settings)


def test_default_branch_prep_writes_merged_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid worktree must merge entry into registry.json and stage a commit."""
    reg_file = tmp_path / "registry.json"
    reg_file.write_text(
        '{"version":"1.0","updated_at":"2026-01-01T00:00:00Z","agents":[]}',
        encoding="utf-8",
    )
    captured_argv: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_argv.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(bot_pr.subprocess, "run", fake_run)
    settings = BotPRSettings(
        owner="o", repo="r", github_token="tok", registry_path_in_repo="registry.json"
    )
    entry = _dummy_entry()
    _default_branch_prep(tmp_path, entry, "https://cdn.example/m.json", settings)

    merged = LiteRegistry.model_validate_json(reg_file.read_text(encoding="utf-8"))
    assert len(merged.agents) == 1
    assert merged.agents[0].id == entry.id
    assert any(argv[:3] == ["git", "-C", str(tmp_path)] and "add" in argv for argv in captured_argv)
    assert any(
        argv[:3] == ["git", "-C", str(tmp_path)] and "commit" in argv for argv in captured_argv
    )


def test_default_branch_prep_rejects_existing_id(tmp_path: Path) -> None:
    """Bot PR prep must not rewrite an existing URN in registry.json."""
    existing = _dummy_entry()
    lr = LiteRegistry(
        version="1.0",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agents=[existing],
    )
    reg_file = tmp_path / "registry.json"
    reg_file.write_text(lr.model_dump_json(), encoding="utf-8")
    settings = BotPRSettings(
        owner="o", repo="r", github_token="tok", registry_path_in_repo="registry.json"
    )
    hijack = RegistryEntry(
        id=existing.id,
        name="Hijack",
        description="attacker listing",
        endpoints={
            "http": "https://evil.example/asap",
            "manifest": "https://evil.example/m",
        },
        skills=["s"],
        asap_version="2.0.0",
    )
    with pytest.raises(ValueError, match="already registered"):
        _default_branch_prep(tmp_path, hijack, "https://evil.example/m.json", settings)
    unchanged = LiteRegistry.model_validate_json(reg_file.read_text(encoding="utf-8"))
    assert unchanged.agents[0].endpoints["http"] == existing.endpoints["http"]


@pytest.mark.asyncio
async def test_github_create_pull_request_requires_token() -> None:
    settings = BotPRSettings(owner="o", repo="r", github_token="")
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        await _github_create_pull_request(
            settings=settings,
            head_branch="h",
            title="t",
            body="b",
            http_client=None,
        )


def test_verification_pending_enum() -> None:
    """Marketplace verification pending aligns with trust badge schema."""
    assert VerificationState.PENDING.value == "pending"


def test_sanitize_urn_non_alphanumeric_defaults_unknown() -> None:
    assert _sanitize_urn_for_branch("@@@") == "unknown"


def test_authenticated_clone_url_without_token_is_unchanged() -> None:
    s = BotPRSettings(owner="a", repo="b", github_token="")
    url = "https://github.com/a/b.git"
    assert s._authenticated_clone_url(url) == url


def test_authenticated_clone_url_non_https_scheme_untouched() -> None:
    s = BotPRSettings(owner="a", repo="b", github_token="secret")
    ssh = "git@github.com:a/b.git"
    assert s._authenticated_clone_url(ssh) == ssh


@pytest.mark.asyncio
async def test_github_create_pull_response_without_html_url_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 42, "number": 7})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        settings = BotPRSettings(owner="o", repo="r", github_token="tok")
        with pytest.raises(RuntimeError, match="html_url"):
            await _github_create_pull_request(
                settings=settings,
                head_branch="h",
                title="t",
                body="b",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_github_create_pull_response_without_number_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        settings = BotPRSettings(owner="o", repo="r", github_token="tok")
        with pytest.raises(RuntimeError, match="pull request number"):
            await _github_create_pull_request(
                settings=settings,
                head_branch="h",
                title="t",
                body="b",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_github_create_pull_request_spawns_client_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in str(request.url):
            return httpx.Response(
                201,
                json={"html_url": "https://github.com/o/r/pull/implicit", "number": 99},
            )
        if "/issues/99/labels" in str(request.url):
            return httpx.Response(200, json=[{"name": AUTO_REGISTRATION_PR_LABEL}])
        return httpx.Response(404, json={"message": "unexpected"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        merged = {**kwargs, "transport": transport}
        return real_client(**merged)

    monkeypatch.setattr("asap.registry.bot_pr.httpx.AsyncClient", factory)
    settings = BotPRSettings(owner="o", repo="r", github_token="tok")
    url = await _github_create_pull_request(
        settings=settings,
        head_branch="auto-reg/x",
        title="t",
        body="b",
        http_client=None,
    )
    assert url == "https://github.com/o/r/pull/implicit"


@pytest.mark.asyncio
async def test_open_registry_pull_request_rejects_reserved_clone_url() -> None:
    """Reserved clone URLs must not reach git clone (SSRF guard)."""
    entry = _dummy_entry()
    settings = BotPRSettings(owner="org", repo="repo", github_token="tok")
    with pytest.raises(ValueError, match="reserved destination"):
        await open_registry_pull_request(
            entry,
            manifest_url="https://cdn.example/m.json",
            settings=settings,
            clone_url="https://127.0.0.1/malicious.git",
        )


@pytest.mark.asyncio
async def test_open_registry_pull_request_threads_then_github_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_to_thread(_fn: Any, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("asap.registry.bot_pr.asyncio.to_thread", fake_to_thread)

    async def fake_gh(
        *,
        settings: BotPRSettings,
        head_branch: str,
        title: str,
        body: str,
        http_client: httpx.AsyncClient | None,
    ) -> str:
        assert settings.owner == "org"
        assert head_branch.startswith("auto-reg/")
        assert "Manifest" in body
        return "https://github.com/org/repo/pull/500"

    monkeypatch.setattr("asap.registry.bot_pr._github_create_pull_request", fake_gh)

    entry = RegistryEntry(
        id="urn:asap:agent:mock-bot",
        name="Mock",
        description="d",
        endpoints={"http": "https://x/asap", "manifest": "https://x/m"},
        skills=["echo"],
        asap_version="2.2.0",
    )
    settings = BotPRSettings(owner="org", repo="repo", github_token="tok")
    result = await open_registry_pull_request(
        entry,
        manifest_url="https://cdn.example/m.json",
        settings=settings,
    )
    assert result.pr_url == "https://github.com/org/repo/pull/500"
    assert result.branch_name.startswith("auto-reg/")
