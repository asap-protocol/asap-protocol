"""Unit tests for JSON-safe Pydantic validation errors.

Raw ``ValidationError.errors()`` can embed a ``ValueError`` in ``ctx``. Putting
that into a JSON-RPC response encodes as ``-32603`` instead of ``-32602``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError, field_validator
from starlette.responses import JSONResponse

from asap.transport._request_handler import json_safe_validation_errors


class _TokenShape(BaseModel):
    """Model whose validator places the original ``ValueError`` in ``ctx``."""

    token: str

    @field_validator("token")
    @classmethod
    def reject_short_token(cls, value: str) -> str:
        if len(value) < 4:
            raise ValueError(f"token shape invalid: {value!r}, expected at least 4 characters")
        return value


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        _TokenShape(token="no")
    return exc_info.value


class TestJsonSafeValidationErrors:
    """``json_safe_validation_errors`` keeps locations and drops live exceptions."""

    def test_raw_errors_are_not_json_serializable(self) -> None:
        error = _validation_error()
        with pytest.raises(TypeError):
            json.dumps(error.errors())

    def test_helper_output_encodes_and_keeps_location(self) -> None:
        error = _validation_error()
        safe = json_safe_validation_errors(error)
        encoded = json.dumps(safe)
        assert json.loads(encoded) == safe
        assert safe[0]["loc"] == ["token"]
        assert "token shape invalid" in safe[0]["msg"]
        assert "no" in safe[0]["msg"]

    def test_helper_output_renders_as_json_response(self) -> None:
        safe = json_safe_validation_errors(_validation_error())
        response = JSONResponse({"validation_errors": safe})
        body = json.loads(response.body)
        assert body["validation_errors"][0]["loc"] == ["token"]
