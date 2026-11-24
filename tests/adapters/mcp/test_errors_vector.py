"""Shared vector for the MCP auth error codes (v2.6.0).

``packages/typescript/mcp-auth/test/errors.test.ts`` reads the same file, so a
change to a code string or to the payload shape on one side fails the test on
the other side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asap.mcp.auth import errors

VECTOR_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "interop" / "mcp-auth-errors.json"
VECTOR: dict[str, Any] = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def _drop_null_fields(value: Any) -> Any:
    """Remove keys whose value is ``None``, recursively.

    ``tool_error_result`` puts ``"annotations": null`` in each content item. The
    vector holds the MCP wire shape, where an absent optional field and a null
    one are compared as equal here.
    """
    if isinstance(value, dict):
        return {key: _drop_null_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_null_fields(item) for item in value]
    return value


def test_vector_lists_the_four_python_codes() -> None:
    """The vector and the Python module define the same four code strings."""
    python_codes = [
        errors.AUTH_REQUIRED,
        errors.INVALID_TOKEN,
        errors.CAPABILITY_DENIED,
        errors.CONSTRAINT_VIOLATION,
    ]
    assert sorted(VECTOR["codes"]) == sorted(python_codes)


@pytest.mark.parametrize("case", VECTOR["tool_error_results"], ids=lambda case: case["code"])
def test_tool_error_result_matches_vector(case: dict[str, Any]) -> None:
    """``tool_error_result`` builds the payload that the vector records."""
    result = errors.tool_error_result(case["code"], case["detail"])
    assert _drop_null_fields(result) == case["result"]
