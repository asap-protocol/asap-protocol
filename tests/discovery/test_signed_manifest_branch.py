"""Lock signed-manifest detection to the presence of both wrapper keys."""

from __future__ import annotations

import pytest

from asap.discovery.validation import (
    ManifestValidationError,
    validate_signed_manifest_response,
)
from asap.models.entities import Manifest


def _plain_manifest() -> dict[str, object]:
    return {
        "id": "urn:asap:agent:test",
        "name": "Test Agent",
        "version": "1.0.0",
        "description": "Test",
        "capabilities": {
            "asap_version": "0.1",
            "skills": [{"id": "echo", "description": "Echo skill"}],
        },
        "endpoints": {"asap": "http://localhost:8000/asap"},
    }


def test_string_signature_field_stays_on_the_plain_manifest_path() -> None:
    """A Manifest ``signature`` string is not a SignedManifest wrapper.

    The signed path requires both ``manifest`` and ``signature``. Treating
    either key as enough would reject this document or verify the wrong object.
    """
    data = _plain_manifest()
    data["signature"] = "not-a-signed-wrapper"
    result = validate_signed_manifest_response(data, verify_signature=True)
    assert isinstance(result, Manifest)
    assert result.id == "urn:asap:agent:test"
    assert result.signature == "not-a-signed-wrapper"


def test_manifest_key_without_signature_stays_on_the_plain_path() -> None:
    """A ``manifest`` key alone does not enter signed-manifest verification."""
    data = {"manifest": _plain_manifest()}
    with pytest.raises(ManifestValidationError, match="Invalid manifest schema"):
        validate_signed_manifest_response(data, verify_signature=True)
