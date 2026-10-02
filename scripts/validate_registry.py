#!/usr/bin/env python3
"""Validate registry.json against the Pydantic Lite Registry schema.

Used by CI (validate-registry.yml) to ensure manual edits to registry.json
do not break the Next.js ISR build or Python discovery client. Agent ids must
be unique (marketplace lookup is first-match).

Accepts:
  - Default (production ``registry.json``): LiteRegistry object with
    ``version``, ``updated_at``, and ``agents``.
  - With ``--allow-agents-array``: root array of RegistryEntry for fixture
    dry-runs (e.g. ``tests/fixtures/registry/shellclaw-v1.0-agents-array.json``).

Exit code: 0 if valid, 1 if invalid (errors to stderr).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

# Add repo src to sys.path when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from asap.discovery.registry import LiteRegistry, RegistryEntry  # noqa: E402


def _duplicate_agent_id_errors(ids_by_index: list[tuple[int, str]]) -> list[str]:
    """Return errors when the same agent URN appears more than once."""
    first_index: dict[str, int] = {}
    errors: list[str] = []
    for index, agent_id in ids_by_index:
        seen_at = first_index.get(agent_id)
        if seen_at is None:
            first_index[agent_id] = index
            continue
        errors.append(
            f"agents[{index}].id: duplicate id {agent_id!r} (first seen at agents[{seen_at}])"
        )
    return errors


_BARE_ARRAY_ERROR = (
    "Root must be a LiteRegistry object with 'version', 'updated_at', and 'agents' "
    "(not a bare agents array). Use --allow-agents-array for fixture dry-runs."
)


def validate_registry(path: Path, *, allow_agents_array: bool = False) -> list[str]:
    if not path.exists():
        return [f"File not found: {path}"]

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    if isinstance(raw, list):
        if not allow_agents_array:
            return [_BARE_ARRAY_ERROR]
        return _validate_agents_list(raw)
    if isinstance(raw, dict) and "agents" in raw:
        return _validate_lite_registry(cast(dict[str, object], raw))
    return [
        "Root must be a LiteRegistry object with 'version', 'updated_at', and 'agents', "
        "or (with --allow-agents-array) a JSON array of agents."
    ]


def _validate_agents_list(agents: list[object]) -> list[str]:
    errors: list[str] = []
    ids_by_index: list[tuple[int, str]] = []
    for i, item in enumerate(agents):
        if not isinstance(item, dict):
            errors.append(f"agents[{i}]: must be an object")
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, str):
            ids_by_index.append((i, raw_id))
        try:
            RegistryEntry.model_validate(item)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"])
                errors.append(f"agents[{i}].{loc}: {err['msg']}")
    errors.extend(_duplicate_agent_id_errors(ids_by_index))
    return errors


def _validate_lite_registry(data: dict[str, object]) -> list[str]:
    try:
        registry = LiteRegistry.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
    return _duplicate_agent_id_errors(
        [(i, str(agent.id)) for i, agent in enumerate(registry.agents)]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate registry.json against the Pydantic Lite Registry schema."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="registry.json",
        type=Path,
        help="Path to registry JSON file (default: registry.json)",
    )
    parser.add_argument(
        "--allow-agents-array",
        action="store_true",
        help="Accept a root JSON array (fixture dry-runs only; not production registry.json).",
    )
    args = parser.parse_args()

    errors = validate_registry(args.file, allow_agents_array=args.allow_agents_array)
    if not errors:
        return 0
    for msg in errors:
        print(msg, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
