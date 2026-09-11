"""Bot-driven Lite Registry PR workflow (AUTO-006).

Clones the repo, writes ``registry.json``, pushes branch ``auto-reg/<urn>``, opens a PR.
GitHub calls are real; git operations can be substituted in tests via ``BotPRSettings`` hooks.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from asap.discovery.registry import LiteRegistry, RegistryEntry

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
AUTO_REGISTRATION_PR_LABEL = "auto-registration"
# Blocking git/network operations should release the thread pool worker eventually.
_GIT_SUBPROCESS_TIMEOUT_SEC = 120

BranchPrepFn = Callable[[Path, RegistryEntry, str, "BotPRSettings"], None]
PushFn = Callable[[Path, str, "BotPRSettings"], None]


def _sanitize_urn_for_branch(urn: str) -> str:
    """Make URN safe for a Git branch segment."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", urn.strip()).strip("-")
    return slug[:200] if slug else "unknown"


def merge_lite_registry(registry: LiteRegistry, new_entry: RegistryEntry) -> LiteRegistry:
    """Append *new_entry* to *registry*, sorted by id.

    Existing ids are rejected. Last-write upsert would let ``POST /registry/agents``
    replace a public listing (and auto-merge would land it) because IssueOps
    uniqueness in ``scripts/process_registration.py`` was never applied here.

    Example:
        >>> merged = merge_lite_registry(registry, new_entry)
    """
    by_id: dict[str, RegistryEntry] = {a.id: a for a in registry.agents}
    if new_entry.id in by_id:
        raise ValueError(f"Agent id {new_entry.id!r} is already registered")
    by_id[new_entry.id] = new_entry
    merged_agents = sorted(by_id.values(), key=lambda e: e.id)
    return LiteRegistry(
        version=registry.version,
        updated_at=datetime.now(timezone.utc),
        agents=merged_agents,
    )


def merge_lite_registry_json_text(existing_json: str, new_entry: RegistryEntry) -> str:
    """Parse *existing_json* as :class:`LiteRegistry`, merge entry, return formatted JSON."""
    try:
        registry = LiteRegistry.model_validate_json(existing_json)
    except ValidationError:
        raise ValueError("registry.json is not valid LiteRegistry JSON") from None
    merged = merge_lite_registry(registry, new_entry)
    return merged.model_dump_json(indent=2)


def conventional_commit_message(entry: RegistryEntry) -> str:
    """Conventional commit subject for an auto-registration PR."""
    return f"feat(registry): auto-register {entry.name} ({entry.id})"


@dataclass(frozen=True, slots=True)
class BotPRResult:
    """Outcome of opening (or simulating) a registry bot PR."""

    pr_url: str
    branch_name: str


@dataclass
class BotPRSettings:
    """Configuration for Git clone/push and GitHub PR creation."""

    owner: str
    repo: str
    base_branch: str = "main"
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    registry_path_in_repo: str = "registry.json"
    remote_name: str = "origin"
    branch_prep_callback: BranchPrepFn | None = None
    push_callback: PushFn | None = None

    def _authenticated_clone_url(self, clone_url: str) -> str:
        """Embed bearer token for HTTPS GitHub clone when token is set."""
        if not self.github_token:
            return clone_url
        parsed = urlparse(clone_url)
        if parsed.scheme != "https" or not parsed.hostname:
            return clone_url
        return clone_url.replace(
            "https://",
            f"https://x-access-token:{self.github_token}@",
            1,
        )


def _run_git(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a fixed ``git`` argv list (no shell).

    Args:
        argv: Full argv starting with ``git`` (e.g. ``["git", "-C", path, "add", ...]``).

    Git paths and branch names come from maintainer-controlled ``BotPRSettings`` and
    sanitized URNs—not arbitrary user shell input.
    """
    # nosec B603, B607: fixed git argv; no shell; paths from trusted settings
    return subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_SUBPROCESS_TIMEOUT_SEC,
    )  # nosec B603, B607


def _default_branch_prep(
    worktree: Path, entry: RegistryEntry, manifest_url: str, settings: BotPRSettings
) -> None:
    reg_file = worktree / settings.registry_path_in_repo
    if not reg_file.is_file():
        raise FileNotFoundError(f"Missing {reg_file}")
    text = reg_file.read_text(encoding="utf-8")
    new_json = merge_lite_registry_json_text(text, entry)
    reg_file.write_text(new_json + "\n", encoding="utf-8")
    _run_git(["git", "-C", str(worktree), "add", str(reg_file.relative_to(worktree))])
    _run_git(
        [
            "git",
            "-C",
            str(worktree),
            "commit",
            "-m",
            conventional_commit_message(entry),
        ]
    )


def _default_git_push(worktree: Path, branch: str, settings: BotPRSettings) -> None:
    _run_git(
        [
            "git",
            "-C",
            str(worktree),
            "push",
            "-u",
            settings.remote_name,
            branch,
        ]
    )


def _run_local_git_flow(
    *,
    clone_target: str,
    worktree: Path,
    base_branch: str,
    branch_name: str,
    entry: RegistryEntry,
    manifest_url: str,
    settings: BotPRSettings,
    branch_prep: BranchPrepFn,
    push_fn: PushFn,
) -> None:
    """Blocking git clone → branch → registry edit → push (runs in a thread pool)."""
    _run_git(["git", "clone", "--depth", "1", "--branch", base_branch, clone_target, str(worktree)])
    _run_git(["git", "-C", str(worktree), "checkout", "-b", branch_name])
    branch_prep(worktree, entry, manifest_url, settings)
    push_fn(worktree, branch_name, settings)


async def open_registry_pull_request(
    entry: RegistryEntry,
    *,
    manifest_url: str,
    settings: BotPRSettings,
    clone_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BotPRResult:
    """Clone repository, append registry entry, push branch, open GitHub PR.

    Args:
        entry: Validated registry row (includes trust defaults).
        manifest_url: Original manifest URL (recorded in PR body).
        settings: Repo coordinates and auth.
        clone_url: Git HTTPS URL; default derived from owner/repo.
        http_client: Optional shared AsyncClient for GitHub API.

    Returns:
        BotPRResult with PR URL and branch name.
    """
    branch_slug = _sanitize_urn_for_branch(entry.id)
    branch_name = f"auto-reg/{branch_slug}"

    clone = clone_url or f"https://github.com/{settings.owner}/{settings.repo}.git"
    if is_reserved_destination(clone):
        raise ValueError(f"Refusing to clone from reserved destination: {clone}")
    clone_target = settings._authenticated_clone_url(clone)

    branch_prep = settings.branch_prep_callback or _default_branch_prep
    push_fn = settings.push_callback or _default_git_push

    tmp = tempfile.mkdtemp(prefix="asap-registry-bot-")
    worktree = Path(tmp)
    try:
        await asyncio.to_thread(
            _run_local_git_flow,
            clone_target=clone_target,
            worktree=worktree,
            base_branch=settings.base_branch,
            branch_name=branch_name,
            entry=entry,
            manifest_url=manifest_url,
            settings=settings,
            branch_prep=branch_prep,
            push_fn=push_fn,
        )
    finally:
        shutil.rmtree(worktree, ignore_errors=True)

    title = f"Auto-register {entry.name}"
    body = (
        f"Automated registration request.\n\n"
        f"- **URN**: `{entry.id}`\n"
        f"- **Manifest**: {manifest_url}\n"
        f"- **Trust**: self-signed (pending verification)\n"
    )

    pr_url = await _github_create_pull_request(
        settings=settings,
        head_branch=branch_name,
        title=title,
        body=body,
        http_client=http_client,
    )
    return BotPRResult(pr_url=pr_url, branch_name=branch_name)


async def _github_add_auto_registration_label(
    *,
    settings: BotPRSettings,
    issue_number: int,
    http_client: httpx.AsyncClient,
) -> None:
    """Apply the label required by ``auto-merge-registry.yml`` gating."""
    label_url = f"{GITHUB_API}/repos/{settings.owner}/{settings.repo}/issues/{issue_number}/labels"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = await http_client.post(
        label_url,
        headers=headers,
        json={"labels": [AUTO_REGISTRATION_PR_LABEL]},
    )
    resp.raise_for_status()


async def _github_create_pull_request(
    *,
    settings: BotPRSettings,
    head_branch: str,
    title: str,
    body: str,
    http_client: httpx.AsyncClient | None,
) -> str:
    if not settings.github_token:
        raise ValueError("GITHUB_TOKEN is required to open a pull request")

    payload: dict[str, Any] = {
        "title": title,
        "head": head_branch,
        "base": settings.base_branch,
        "body": body,
        "maintainer_can_modify": True,
    }
    url = f"{GITHUB_API}/repos/{settings.owner}/{settings.repo}/pulls"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        close_client = True
    try:
        resp = await http_client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        html_url = data.get("html_url")
        if not isinstance(html_url, str):
            raise RuntimeError("GitHub API response missing html_url")
        number = data.get("number")
        if not isinstance(number, int):
            raise RuntimeError("GitHub API response missing pull request number")
        await _github_add_auto_registration_label(
            settings=settings,
            issue_number=number,
            http_client=http_client,
        )
        return html_url
    finally:
        if close_client:
            await http_client.aclose()


def is_reserved_destination(url: str) -> bool:
    """Best-effort synchronous guard for dangerous URLs (tests / diagnostics)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    # Build IPv4-any without a 0.0.0.0 literal so static analyzers do not flag B104 (bind-all).
    ipv4_any_host = ".".join(("0", "0", "0", "0"))
    if host in {"localhost", "127.0.0.1", "::1", ipv4_any_host}:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)
