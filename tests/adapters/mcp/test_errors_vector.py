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
    assert result == case["result"]
