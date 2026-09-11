#!/usr/bin/env python3
"""Evaluate whether an auto-registration PR may enable squash auto-merge.

Policy:
- ``registry.json`` must already validate as :class:`~asap.discovery.registry.LiteRegistry`
  (run ``scripts/validate_registry.py`` first in CI).
- **Add-only**: head may introduce new agent ids. It must not modify or delete ids that
  already exist in the base revision (overwrites hijack traffic; deletions drop listings).
  Duplicate ids in base or head are ineligible (dict last-wins would miss a prepended
  hijack; marketplace ``find_by_id`` is first-match).
- **Self-signed path** (registry terms): no new **verified** marketplace badge.
  New agents must not ship with ``verification.status == "verified"``.
  Promotions stay on the manual verification flow.

Exit code ``0`` = eligible for auto-merge; ``1`` = requires human review. Reason printed to
stdout (and stderr on failure).

Run from the repo root with ``uv run python scripts/check_auto_registration_merge_eligible.py``
so the editable ``asap`` package is on ``sys.path``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from asap.discovery.registry import LiteRegistry, RegistryEntry
from asap.models.enums import VerificationState


def _is_verified(entry: RegistryEntry) -> bool:
    v = entry.verification
    return v is not None and v.status == VerificationState.VERIFIED


def _load(path: Path) -> LiteRegistry:
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        agents = [RegistryEntry.model_validate(cast(dict[str, object], item)) for item in raw]
        return LiteRegistry(
            version="1.0",
            updated_at=datetime.fromtimestamp(0, tz=UTC),
            agents=agents,
        )
    return LiteRegistry.model_validate(cast(dict[str, object], raw))


def _entry_payload(entry: RegistryEntry) -> dict[str, object]:
    return entry.model_dump(mode="json")


def _agents_by_unique_id(
    agents: list[RegistryEntry], *, source: str
) -> tuple[dict[str, RegistryEntry] | None, str]:
    """Index agents by id, or return an ineligible reason if ids are duplicated."""
    by_id: dict[str, RegistryEntry] = {}
    for agent in agents:
        aid = str(agent.id)
        if aid in by_id:
            return None, (
                f"Duplicate agent id {aid} in {source} registry.json; "
                "auto-registration requires unique ids."
            )
        by_id[aid] = agent
    return by_id, ""


def evaluate(base_path: Path, head_path: Path) -> tuple[bool, str]:
    """Return whether *head_path* is an add-only self-signed registry update."""
    try:
        base = _load(base_path)
        head = _load(head_path)
    except (json.JSONDecodeError, ValidationError, OSError) as e:
        return False, f"Failed to parse registry JSON: {e}"

    base_by_id, base_err = _agents_by_unique_id(base.agents, source="base")
    if base_by_id is None:
        return False, base_err
    head_by_id, head_err = _agents_by_unique_id(head.agents, source="head")
    if head_by_id is None:
        return False, head_err

    for aid, agent in head_by_id.items():
        prev = base_by_id.get(aid)
        if prev is None:
            if _is_verified(agent):
                return (
                    False,
                    f"New agent {aid} must not use verification.status=verified "
                    "(self-signed / auto-registration path only).",
                )
            continue
        if _entry_payload(prev) != _entry_payload(agent):
            return (
                False,
                f"Agent {aid} is already registered; auto-registration cannot "
                "modify existing entries.",
            )

    for aid in base_by_id:
        if aid not in head_by_id:
            return (
                False,
                f"Agent {aid} is missing from the PR; auto-registration cannot remove entries.",
            )
    return True, "Auto-merge eligible: add-only self-signed registration."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-file",
        type=Path,
        required=True,
        help="Path to base revision registry.json",
    )
    parser.add_argument(
        "--head-file",
        type=Path,
        required=True,
        help="Path to PR head registry.json",
    )
    args = parser.parse_args()
    ok, message = evaluate(args.base_file, args.head_file)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
