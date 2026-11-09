"""Tests for asap.discovery.validation module.

Covers ManifestValidationError, validate_manifest_schema,
check_version_compatibility, manifest_supports_skill, and require_skill.
Focus on invalid inputs and edge cases (security-sensitive validation).
"""

from __future__ import annotations

import pytest

from asap.crypto.keys import generate_keypair
from asap.crypto.signing import sign_manifest
from asap.discovery.validation import (
    ManifestValidationError,
    check_version_compatibility,
    manifest_supports_skill,
    require_skill,
    validate_manifest_schema,
    validate_signed_manifest_response,
)
from asap.errors import SignatureVerificationError
from asap.models.constants import ASAP_PROTOCOL_VERSION
from asap.models.entities import Capability, Endpoint, Manifest, Skill


_DEFAULT_SKILLS = [Skill(id="echo", description="Echo skill")]


def _make_manifest(
    asap_version: str = "0.1",
    skills: list[Skill] | None = None,
) -> Manifest:
    """Build a minimal valid Manifest for testing."""
    return Manifest(
        id="urn:asap:agent:test",
        name="Test Agent",
        version="1.0.0",
        description="Test",
        capabilities=Capability(
            asap_version=asap_version,
            skills=_DEFAULT_SKILLS if skills is None else skills,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _make_valid_manifest_dict() -> dict[str, object]:
    """Build a raw dict that validates as a valid Manifest."""
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


# --------------------------------------------------------------------------- #
# ManifestValidationError
# --------------------------------------------------------------------------- #


class TestManifestValidationError:
    """Tests for ManifestValidationError exception."""

    def test_error_with_message_only(self) -> None:
        """Error stores message and field defaults to None."""
        err = ManifestValidationError("something broke")
        assert str(err) == "something broke"
        assert err.message == "something broke"
        assert err.field is None

    def test_error_with_message_and_field(self) -> None:
        """Error stores both message and field."""
        err = ManifestValidationError("bad version", field="capabilities.asap_version")
        assert err.message == "bad version"
        assert err.field == "capabilities.asap_version"

    def test_error_is_exception(self) -> None:
        """ManifestValidationError is a subclass of Exception."""
        assert issubclass(ManifestValidationError, Exception)


# --------------------------------------------------------------------------- #
# validate_manifest_schema
# --------------------------------------------------------------------------- #


class TestValidateManifestSchema:
    """Tests for validate_manifest_schema."""

    def test_valid_manifest_returns_manifest(self) -> None:
        """Valid dict returns a Manifest instance."""
        data = _make_valid_manifest_dict()
        result = validate_manifest_schema(data)
        assert isinstance(result, Manifest)
        assert result.id == "urn:asap:agent:test"

    def test_empty_dict_raises(self) -> None:
        """Empty dict raises ManifestValidationError."""
        with pytest.raises(ManifestValidationError, match="Invalid manifest schema") as exc_info:
            validate_manifest_schema({})
        assert exc_info.value.field == "schema"

    def test_missing_required_field_raises(self) -> None:
        """Missing required field (e.g. 'id') raises ManifestValidationError."""
        data = _make_valid_manifest_dict()
        del data["id"]
        with pytest.raises(ManifestValidationError):
            validate_manifest_schema(data)

    def test_wrong_type_raises(self) -> None:
        """Wrong type for a field raises ManifestValidationError."""
        data = _make_valid_manifest_dict()
        data["version"] = 123  # should be string
        with pytest.raises(ManifestValidationError):
            validate_manifest_schema(data)

    def test_invalid_capabilities_raises(self) -> None:
        """Invalid capabilities structure raises ManifestValidationError."""
        data = _make_valid_manifest_dict()
        data["capabilities"] = "not a dict"
        with pytest.raises(ManifestValidationError):
            validate_manifest_schema(data)

    def test_invalid_sla_percentage_raises(self) -> None:
        """A manifest SLA percentage that is not numeric fails schema validation."""
        data = _make_valid_manifest_dict()
        data["sla"] = {"availability": "not-a-percent", "max_error_rate": ""}
        with pytest.raises(ManifestValidationError, match="Invalid percentage string"):
            validate_manifest_schema(data)

    def test_missing_endpoints_raises(self) -> None:
        """Missing endpoints field raises ManifestValidationError."""
        data = _make_valid_manifest_dict()
        del data["endpoints"]
        with pytest.raises(ManifestValidationError):
            validate_manifest_schema(data)

    def test_error_preserves_pydantic_cause(self) -> None:
        """ManifestValidationError wraps original ValidationError as __cause__."""
        from pydantic import ValidationError

        with pytest.raises(ManifestValidationError) as exc_info:
            validate_manifest_schema({})
        assert isinstance(exc_info.value.__cause__, ValidationError)


# --------------------------------------------------------------------------- #
# check_version_compatibility
# --------------------------------------------------------------------------- #


class TestCheckVersionCompatibility:
    """Tests for check_version_compatibility."""

    def test_compatible_version_does_not_raise(self) -> None:
        """Compatible version passes silently."""
        manifest = _make_manifest(asap_version="0.1")
        check_version_compatibility(manifest)  # should not raise

    def test_same_version_does_not_raise(self) -> None:
        """Exact match on version passes silently."""
        manifest = _make_manifest(asap_version=ASAP_PROTOCOL_VERSION)
        check_version_compatibility(manifest, min_asap_version=ASAP_PROTOCOL_VERSION)

    def test_newer_version_does_not_raise(self) -> None:
        """Newer agent version passes when min is lower."""
        manifest = _make_manifest(asap_version="1.0")
        check_version_compatibility(manifest, min_asap_version="0.1")

    def test_older_version_raises(self) -> None:
        """Older agent version raises ManifestValidationError."""
        manifest = _make_manifest(asap_version="0.1")
        with pytest.raises(ManifestValidationError, match="older than required") as exc_info:
            check_version_compatibility(manifest, min_asap_version="1.0")
        assert exc_info.value.field == "capabilities.asap_version"

    def test_invalid_agent_version_raises(self) -> None:
        """Invalid version string raises ManifestValidationError."""
        manifest = _make_manifest(asap_version="not-a-version")
        with pytest.raises(ManifestValidationError, match="Invalid ASAP version") as exc_info:
            check_version_compatibility(manifest)
        assert exc_info.value.field == "capabilities.asap_version"

    def test_defaults_to_protocol_version(self) -> None:
        """Uses ASAP_PROTOCOL_VERSION when min_asap_version is None."""
        manifest = _make_manifest(asap_version=ASAP_PROTOCOL_VERSION)
        # Should not raise since agent version == protocol version
        check_version_compatibility(manifest, min_asap_version=None)

    def test_custom_min_version(self) -> None:
        """Custom min_asap_version is respected."""
        manifest = _make_manifest(asap_version="0.5")
        check_version_compatibility(manifest, min_asap_version="0.3")
        with pytest.raises(ManifestValidationError):
            check_version_compatibility(manifest, min_asap_version="0.9")


# --------------------------------------------------------------------------- #
# manifest_supports_skill
# --------------------------------------------------------------------------- #


class TestManifestSupportsSkill:
    """Tests for manifest_supports_skill."""

    def test_returns_true_for_existing_skill(self) -> None:
        """Returns True when skill_id is in the manifest's skills."""
        manifest = _make_manifest(skills=[Skill(id="echo", description="Echo")])
        assert manifest_supports_skill(manifest, "echo") is True

    def test_returns_false_for_missing_skill(self) -> None:
        """Returns False when skill_id is not in the manifest's skills."""
        manifest = _make_manifest(skills=[Skill(id="echo", description="Echo")])
        assert manifest_supports_skill(manifest, "web_research") is False

    def test_returns_false_for_empty_skills(self) -> None:
        """Returns False when manifest has no skills."""
        manifest = _make_manifest(skills=[])
        assert manifest_supports_skill(manifest, "echo") is False

    def test_multiple_skills(self) -> None:
        """Returns True for each skill in a multi-skill manifest."""
        skills = [
            Skill(id="echo", description="Echo"),
            Skill(id="search", description="Search"),
            Skill(id="translate", description="Translate"),
        ]
        manifest = _make_manifest(skills=skills)
        assert manifest_supports_skill(manifest, "echo") is True
        assert manifest_supports_skill(manifest, "search") is True
        assert manifest_supports_skill(manifest, "translate") is True
        assert manifest_supports_skill(manifest, "nonexistent") is False


# --------------------------------------------------------------------------- #
# require_skill
# --------------------------------------------------------------------------- #


class TestRequireSkill:
    """Tests for require_skill."""

    def test_does_not_raise_for_existing_skill(self) -> None:
        """No exception when the required skill exists."""
        manifest = _make_manifest(skills=[Skill(id="echo", description="Echo")])
        require_skill(manifest, "echo")  # should not raise

    def test_raises_for_missing_skill(self) -> None:
        """Raises ManifestValidationError with available skills list."""
        manifest = _make_manifest(skills=[Skill(id="echo", description="Echo")])
        with pytest.raises(
            ManifestValidationError, match="does not support required skill"
        ) as exc_info:
            require_skill(manifest, "web_research")
        assert exc_info.value.field == "capabilities.skills"
        assert "echo" in str(exc_info.value)

    def test_raises_for_empty_skills_shows_none(self) -> None:
        """Raises with 'none' in message when manifest has no skills."""
        manifest = _make_manifest(skills=[])
        with pytest.raises(ManifestValidationError, match="none"):
            require_skill(manifest, "anything")

    def test_error_lists_available_skills(self) -> None:
        """Error message lists all available skills."""
        skills = [
            Skill(id="alpha", description="A"),
            Skill(id="beta", description="B"),
        ]
        manifest = _make_manifest(skills=skills)
        with pytest.raises(ManifestValidationError) as exc_info:
            require_skill(manifest, "gamma")
        msg = str(exc_info.value)
        assert "alpha" in msg
        assert "beta" in msg


# --------------------------------------------------------------------------- #
# validate_signed_manifest_response
# --------------------------------------------------------------------------- #


class TestValidateSignedManifestResponse:
    """Tests for validate_signed_manifest_response (plain or signed manifest dict)."""

    def test_plain_manifest_dict_returns_manifest(self) -> None:
        """Plain manifest dict (no signature) returns validated Manifest."""
        data = _make_valid_manifest_dict()
        result = validate_signed_manifest_response(data, verify_signature=False)
        assert isinstance(result, Manifest)
        assert result.id == "urn:asap:agent:test"

    def test_signed_manifest_with_verify_returns_inner_manifest(self) -> None:
        """Signed manifest dict with verify_signature=True returns inner Manifest when valid."""
        manifest = _make_manifest()
        private_key, _ = generate_keypair()
        signed = sign_manifest(manifest, private_key)
        data = signed.model_dump(mode="json")
        result = validate_signed_manifest_response(data, verify_signature=True)
        assert isinstance(result, Manifest)
        assert result.id == manifest.id

    def test_signed_manifest_verify_false_returns_inner_without_verifying(self) -> None:
        """Signed manifest with verify_signature=False returns inner manifest without verifying."""
        manifest = _make_manifest()
        private_key, _ = generate_keypair()
        signed = sign_manifest(manifest, private_key)
        data = signed.model_dump(mode="json")
        result = validate_signed_manifest_response(data, verify_signature=False)
        assert result.id == manifest.id

    def test_signed_manifest_tampered_raises_when_verify_true(self) -> None:
        """Signed manifest with tampered content raises SignatureVerificationError when verify_signature=True."""
        manifest = _make_manifest()
        private_key, _ = generate_keypair()
        signed = sign_manifest(manifest, private_key)
        data = signed.model_dump(mode="json")
        data["manifest"] = {**data["manifest"], "name": "Tampered"}
        with pytest.raises(SignatureVerificationError, match="tamper|invalid"):
            validate_signed_manifest_response(data, verify_signature=True)

    def test_invalid_signed_schema_raises_manifest_validation_error(self) -> None:
        """Dict with manifest+signature but invalid schema raises ManifestValidationError."""
        data = _make_valid_manifest_dict()
        data["signature"] = {"alg": "ed25519", "signature": "x"}
        data["manifest"] = data.copy()
        del data["manifest"]["id"]
        with pytest.raises(ManifestValidationError, match="schema"):
            validate_signed_manifest_response(data, verify_signature=False)
