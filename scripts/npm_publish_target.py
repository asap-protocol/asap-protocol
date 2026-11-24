#!/usr/bin/env python3
"""Resolve an npm release tag to the package it publishes.

``.github/workflows/publish-typescript.yml`` runs this on a tag push. Each npm
package has its own tag prefix. The version in the tag must equal the version in
that package's ``package.json``; the workflow never rewrites it. A prerelease
version publishes under the ``rc`` dist-tag, so ``latest`` never moves to a
prerelease. A published dependency field must not hold a pnpm ``workspace:``
range, because ``npm publish`` does not rewrite it.

On success it prints GitHub Actions outputs (``key=value``); otherwise it prints
the reason to stderr and exits 1. Standard library only, so the publish job runs
it with the runner's ``python3``.

Example:
    $ python3 scripts/npm_publish_target.py client-v2.5.0
    package_name=@asap-protocol/client
    package_dir=packages/typescript/client
    version=2.5.0
    dist_tag=latest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Tag prefix -> (npm package name, package directory). A new package needs an
# entry here, a tag pattern in publish-typescript.yml, and a Trusted Publisher.
PACKAGES_BY_TAG_PREFIX: dict[str, tuple[str, str]] = {
    "client-v": ("@asap-protocol/client", "packages/typescript/client"),
    "mcp-auth-v": ("@asap-protocol/mcp-auth", "packages/typescript/mcp-auth"),
}

# SemVer 2.0.0 with an optional prerelease. Build metadata is not allowed in a tag.
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"
)
_PUBLISHED_DEPENDENCY_FIELDS = ("dependencies", "peerDependencies", "optionalDependencies")
PRERELEASE_DIST_TAG = "rc"
STABLE_DIST_TAG = "latest"


class PublishTargetError(ValueError):
    """The tag cannot be published as it is."""


@dataclass(frozen=True)
class PublishTarget:
    """What the publish job builds and publishes for one tag."""

    package_name: str
    package_dir: str
    version: str
    dist_tag: str

    def github_outputs(self) -> str:
        """Lines for ``$GITHUB_OUTPUT``."""
        return "\n".join(
            f"{key}={value}"
            for key, value in (
                ("package_name", self.package_name),
                ("package_dir", self.package_dir),
                ("version", self.version),
                ("dist_tag", self.dist_tag),
            )
        )


def _check_manifest(tag: str, manifest: dict[str, Any], package_name: str, version: str) -> None:
    if manifest.get("name") != package_name:
        raise PublishTargetError(
            f"tag {tag!r}: package.json name is {manifest.get('name')!r}, expected {package_name!r}"
        )
    if manifest.get("version") != version:
        raise PublishTargetError(
            f"tag {tag!r} does not match version {manifest.get('version')!r} in package.json; "
            "bump package.json in a commit, then tag that commit"
        )
    for field in _PUBLISHED_DEPENDENCY_FIELDS:
        for dependency, spec in manifest.get(field, {}).items():
            if str(spec).startswith("workspace:"):
                raise PublishTargetError(
                    f"tag {tag!r}: {field} has {dependency!r}: {spec!r}; a workspace: range "
                    "is not rewritten by npm publish, use a SemVer range"
                )


def resolve_publish_target(tag: str, repo_root: Path = REPO_ROOT) -> PublishTarget:
    """Map a tag such as ``mcp-auth-v0.1.0`` to its package, or raise PublishTargetError.

    Example:
        >>> resolve_publish_target("client-v2.5.0").dist_tag
        'latest'
    """
    prefix = next((p for p in PACKAGES_BY_TAG_PREFIX if tag.startswith(p)), None)
    if prefix is None:
        raise PublishTargetError(
            f"tag {tag!r} names no npm package; expected one of "
            f"{sorted(PACKAGES_BY_TAG_PREFIX)} followed by a version"
        )
    version = tag.removeprefix(prefix)
    if not _SEMVER_PATTERN.fullmatch(version):
        raise PublishTargetError(
            f"tag {tag!r}: {version!r} is not a SemVer version such as 2.5.0 or 0.1.0-rc.1"
        )
    package_name, package_dir = PACKAGES_BY_TAG_PREFIX[prefix]
    manifest_path = repo_root / package_dir / "package.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    _check_manifest(tag, manifest, package_name, version)
    dist_tag = PRERELEASE_DIST_TAG if "-" in version else STABLE_DIST_TAG
    return PublishTarget(package_name, package_dir, version, dist_tag)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: print the outputs for the tag, or the reason it cannot be published."""
    parser = argparse.ArgumentParser(description="Resolve an npm release tag to its package.")
    parser.add_argument("tag", help="the pushed tag, for example client-v2.5.0 ($GITHUB_REF_NAME)")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        target = resolve_publish_target(args.tag, args.repo_root)
    except PublishTargetError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    print(target.github_outputs())
    return 0


if __name__ == "__main__":
    sys.exit(main())
