"""Tests for scripts/npm_publish_target.py and the npm tag triggers.

``publish-typescript.yml`` cannot run locally, so the tag → package → version
logic lives in the script and is tested here, and the workflow triggers are read
from the YAML files.
"""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships with the docs toolchain; no stubs installed

from scripts.npm_publish_target import (
    PACKAGES_BY_TAG_PREFIX,
    PublishTarget,
    PublishTargetError,
    main,
    resolve_publish_target,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
NPM_TAGS = ["client-v2.5.0", "mcp-auth-v0.1.0", "mcp-auth-v0.1.0-rc.1"]


def _write_package(root: Path, package_dir: str, manifest: dict[str, Any]) -> None:
    path = root / package_dir / "package.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo root with the two tag-published packages at 2.5.0 and 0.1.0-rc.1."""
    _write_package(
        tmp_path,
        "packages/typescript/client",
        {"name": "@asap-protocol/client", "version": "2.5.0"},
    )
    _write_package(
        tmp_path,
        "packages/typescript/mcp-auth",
        {
            "name": "@asap-protocol/mcp-auth",
            "version": "0.1.0-rc.1",
            "peerDependencies": {"@asap-protocol/client": "^2.5.0"},
            "devDependencies": {"@asap-protocol/client": "workspace:*"},
        },
    )
    return tmp_path


def test_a_stable_tag_publishes_its_package_under_latest(repo: Path) -> None:
    assert resolve_publish_target("client-v2.5.0", repo) == PublishTarget(
        package_name="@asap-protocol/client",
        package_dir="packages/typescript/client",
        version="2.5.0",
        dist_tag="latest",
    )


def test_a_prerelease_tag_publishes_under_rc(repo: Path) -> None:
    target = resolve_publish_target("mcp-auth-v0.1.0-rc.1", repo)

    assert (target.package_name, target.dist_tag) == ("@asap-protocol/mcp-auth", "rc")


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("client-v2.5.1", "tag 'client-v2.5.1' does not match version '2.5.0'"),
        ("mcp-auth-v0.1.0", "tag 'mcp-auth-v0.1.0' does not match version '0.1.0-rc.1'"),
        ("v2.6.0", "names no npm package"),
        ("mastra-v2.4.2", "names no npm package"),
        ("client-v2.5", "is not a SemVer version"),
        ("client-v02.5.0", "is not a SemVer version"),
        ("client-v2.5.0+build.1", "is not a SemVer version"),
    ],
)
def test_a_tag_that_does_not_fit_fails(repo: Path, tag: str, message: str) -> None:
    with pytest.raises(PublishTargetError, match=message):
        resolve_publish_target(tag, repo)


def test_a_package_name_that_differs_from_the_prefix_fails(repo: Path) -> None:
    (repo / "packages/typescript/client/package.json").write_text(
        '{"name": "client", "version": "2.5.0"}'
    )

    with pytest.raises(PublishTargetError, match="expected '@asap-protocol/client'"):
        resolve_publish_target("client-v2.5.0", repo)


@pytest.mark.parametrize("field", ["dependencies", "peerDependencies", "optionalDependencies"])
def test_a_workspace_range_in_a_published_field_fails(repo: Path, field: str) -> None:
    manifest = {
        "name": "@asap-protocol/mcp-auth",
        "version": "0.1.0-rc.1",
        field: {"@asap-protocol/client": "workspace:*"},
    }
    (repo / "packages/typescript/mcp-auth/package.json").write_text(json.dumps(manifest))

    with pytest.raises(PublishTargetError, match=f"{field}.*workspace:"):
        resolve_publish_target("mcp-auth-v0.1.0-rc.1", repo)


@pytest.mark.parametrize("prefix", sorted(PACKAGES_BY_TAG_PREFIX))
def test_the_packages_in_this_repo_can_be_published_from_their_tag(prefix: str) -> None:
    """The shipped manifests pass: matching name, no workspace: range in a published field."""
    _name, package_dir = PACKAGES_BY_TAG_PREFIX[prefix]
    version = json.loads((REPO_ROOT / package_dir / "package.json").read_text())["version"]

    assert resolve_publish_target(f"{prefix}{version}").version == version


def test_main_prints_github_outputs(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["mcp-auth-v0.1.0-rc.1", "--repo-root", str(repo)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "package_name=@asap-protocol/mcp-auth",
        "package_dir=packages/typescript/mcp-auth",
        "version=0.1.0-rc.1",
        "dist_tag=rc",
    ]


def test_main_fails_with_the_reason_on_stderr(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["client-v9.9.9", "--repo-root", str(repo)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not match version '2.5.0'" in captured.err


def _push_tag_patterns(workflow: str) -> list[str]:
    config = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    triggers = config.get("on", config.get(True))  # YAML 1.1 reads a bare `on` key as True
    return list(triggers["push"]["tags"])


def _matches(tag: str, patterns: list[str]) -> bool:
    """GitHub filter patterns match the whole tag name; with `*` as the only special
    character (true for these workflows), ``fnmatchcase`` gives the same answer."""
    assert all(set(pattern) & set("?[]!+") == set() for pattern in patterns), patterns
    return any(fnmatchcase(tag, pattern) for pattern in patterns)


@pytest.mark.parametrize("tag", NPM_TAGS)
def test_release_yml_does_not_react_to_npm_tags(tag: str) -> None:
    """release.yml (`v*`) builds PyPI and Docker; a per-package npm tag must not start it."""
    assert not _matches(tag, _push_tag_patterns("release.yml"))


@pytest.mark.parametrize("tag", NPM_TAGS)
def test_publish_typescript_reacts_to_npm_tags(tag: str) -> None:
    assert _matches(tag, _push_tag_patterns("publish-typescript.yml"))


def test_the_python_release_tag_does_not_publish_npm() -> None:
    assert not _matches("v2.6.0", _push_tag_patterns("publish-typescript.yml"))


def test_only_publish_typescript_publishes_to_npm() -> None:
    """One workflow runs a real npm publish; the Trusted Publisher names that file.

    The v2.3.* hotfix workflow (hotfix-release.yml) is retired: a package patch
    ships from its own tag through publish-typescript.yml.
    """
    publishing = sorted(
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if "npm publish --access public --provenance" in path.read_text(encoding="utf-8")
    )

    assert publishing == ["publish-typescript.yml"]
