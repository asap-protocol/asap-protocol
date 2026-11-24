"""Branch changes must start the checks used by the release stack."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize(
    "workflow",
    [
        "ci",
        "mastra-ci",
        "openai-agents-ci",
        "publish-typescript",
        "starters-smoke",
        "typescript-sdk",
    ],
)
def test_ci_runs_when_a_pull_request_moves_to_the_release_branch(workflow: str) -> None:
    # BaseLoader preserves the GitHub Actions key "on" as a string.
    document = yaml.load((WORKFLOWS / f"{workflow}.yml").read_text(), Loader=yaml.BaseLoader)
    trigger = document["on"]["pull_request"]
    assert "release/2.6.0" in trigger["branches"]
    assert {"opened", "reopened", "synchronize", "edited"} <= set(trigger.get("types", []))
