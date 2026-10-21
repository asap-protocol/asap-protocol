"""Tests for signing utilities and related features."""

from __future__ import annotations
import base64
import jcs
import pytest
from pydantic import ValidationError


from asap.crypto.keys import generate_keypair, public_key_to_base64

from asap.crypto.models import SignatureBlock, SignedManifest
from asap.crypto.signing import canonicalize, sign_manifest, verify_manifest
from asap.errors import SignatureVerificationError
from asap.models.entities import Capability, Endpoint, Manifest, Skill


def _sample_manifest(signature: str | None = None) -> Manifest:
    return Manifest(
        id="urn:asap:agent:test-signing",
        name="Test Agent",
        version="1.0.0",
        description="Agent for signing tests",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo skill")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
        signature=signature,
    )


def test_canonicalize_returns_bytes() -> None:
    manifest = _sample_manifest()
    result = canonicalize(manifest)
    assert isinstance(result, bytes)
    assert result.decode("utf-8").startswith("{")


def test_canonicalize_deterministic() -> None:
    manifest = _sample_manifest()
    a = canonicalize(manifest)
    b = canonicalize(manifest)
    assert a == b


def test_canonicalize_same_content_different_key_order() -> None:
    manifest = _sample_manifest()
    payload = manifest.model_dump(exclude={"signature"})
    reordered = {k: payload[k] for k in reversed(list(payload.keys()))}
    from_canonicalize = canonicalize(manifest)
    from_jcs = jcs.canonicalize(reordered)
    assert from_canonicalize == from_jcs


def test_canonicalize_excludes_signature() -> None:
    manifest_with_sig = _sample_manifest(signature="would-be-signature")
    result = canonicalize(manifest_with_sig)
    assert b"would-be-signature" not in result


def test_sign_manifest_returns_signed_manifest() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    assert isinstance(signed, SignedManifest)
    assert signed.manifest == manifest
    assert signed.signature.alg == "ed25519"
    assert isinstance(signed.signature.signature, str)


def test_sign_manifest_signature_64_bytes() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    raw = base64.b64decode(signed.signature.signature)
    assert len(raw) == 64


def test_sign_manifest_deterministic() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    a = sign_manifest(manifest, private_key)
    b = sign_manifest(manifest, private_key)
    assert a.signature.signature == b.signature.signature


def test_sign_manifest_verify_with_public_key() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    payload_bytes = canonicalize(manifest)
    raw_sig = base64.b64decode(signed.signature.signature)
    public_key.verify(raw_sig, payload_bytes)


def test_signature_block_accepts_ed25519() -> None:
    block = SignatureBlock(alg="ed25519", signature="A" * 88)  # 64 bytes base64
    assert block.alg == "ed25519"
    assert block.signature == "A" * 88


def test_signature_block_rejects_unknown_algorithm() -> None:
    with pytest.raises(ValidationError):
        SignatureBlock(alg="rs256", signature="x")
    with pytest.raises(ValidationError):
        SignatureBlock(alg="HS256", signature="x")


def test_signature_block_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SignatureBlock(alg="ed25519", signature="x", key_id="k1")


def test_signed_manifest_structure() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    d = signed.model_dump()
    assert "manifest" in d
    assert "signature" in d
    assert d["signature"]["alg"] == "ed25519"
    assert "signature" in d["signature"]


# --- Verification (verify_manifest) ---


def test_verify_manifest_valid_with_embedded_public_key() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    assert verify_manifest(signed) is True


def test_verify_manifest_valid_with_explicit_public_key() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    signed_no_pk = SignedManifest(
        manifest=signed.manifest,
        signature=signed.signature,
        public_key=None,
    )
    assert verify_manifest(signed_no_pk, public_key=public_key) is True


def test_verify_manifest_unsupported_alg_raises() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    bad_block = SignatureBlock.model_construct(alg="rs256", signature=signed.signature.signature)
    bad_signed = SignedManifest.model_construct(
        manifest=signed.manifest,
        signature=bad_block,
        public_key=public_key_to_base64(public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(bad_signed)
    assert "ed25519" in exc_info.value.message
    assert exc_info.value.code == "asap:error/signature-verification"
    assert exc_info.value.details.get("alg") == "rs256"


def test_verify_manifest_missing_public_key_raises() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    signed_no_pk = SignedManifest(
        manifest=signed.manifest,
        signature=signed.signature,
        public_key=None,
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(signed_no_pk)
    assert (
        "no public key" in exc_info.value.message.lower() or "public_key" in exc_info.value.message
    )


def test_verify_manifest_rejects_truncated_public_key() -> None:
    """A valid base64 key of the wrong length is a signature failure, not a crash."""
    manifest = _sample_manifest()
    private_key, _public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    truncated = base64.b64encode(b"\x11" * 16).decode("ascii")
    signed_short_key = SignedManifest(
        manifest=signed.manifest,
        signature=signed.signature,
        public_key=truncated,
    )
    with pytest.raises(SignatureVerificationError, match="32 bytes, got 16"):
        verify_manifest(signed_short_key)


def test_verify_manifest_invalid_public_key_raises() -> None:
    manifest = _sample_manifest()
    private_key, _ = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    signed_bad_pk = SignedManifest.model_construct(
        manifest=signed.manifest,
        signature=signed.signature,
        public_key="not-valid-base64-key!!!",
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(signed_bad_pk)
    assert "public" in exc_info.value.message.lower() or "Invalid" in exc_info.value.message


def test_verify_manifest_invalid_signature_base64_raises() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    bad_block = SignatureBlock.model_construct(alg="ed25519", signature="not-valid-base64!!!")
    bad_signed = SignedManifest(
        manifest=signed.manifest,
        signature=bad_block,
        public_key=public_key_to_base64(public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(bad_signed)
    assert (
        "base64" in exc_info.value.message.lower() or "encoding" in exc_info.value.message.lower()
    )


def test_verify_manifest_signature_wrong_length_raises() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    short_sig_b64 = base64.b64encode(b"x" * 32).decode("ascii")
    bad_block = SignatureBlock(alg="ed25519", signature=short_sig_b64)
    bad_signed = SignedManifest(
        manifest=signed.manifest,
        signature=bad_block,
        public_key=public_key_to_base64(public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(bad_signed)
    assert "64 bytes" in exc_info.value.message
    assert exc_info.value.details.get("signature_length") == 32


def test_verify_manifest_tampered_manifest_raises() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    tampered_manifest = Manifest(
        id=signed.manifest.id,
        name="Tampered Name",
        version=signed.manifest.version,
        description=signed.manifest.description,
        capabilities=signed.manifest.capabilities,
        endpoints=signed.manifest.endpoints,
        signature=signed.manifest.signature,
    )
    tampered_signed = SignedManifest(
        manifest=tampered_manifest,
        signature=signed.signature,
        public_key=public_key_to_base64(public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(tampered_signed)
    assert "tamper" in exc_info.value.message.lower() or "invalid" in exc_info.value.message.lower()


def test_verify_manifest_tampered_signature_raises() -> None:
    manifest = _sample_manifest()
    private_key, public_key = generate_keypair()
    signed = sign_manifest(manifest, private_key)
    raw_sig = base64.b64decode(signed.signature.signature)
    corrupted = bytes(b ^ 0xFF for b in raw_sig)
    corrupted_b64 = base64.b64encode(corrupted).decode("ascii")
    bad_block = SignatureBlock(alg="ed25519", signature=corrupted_b64)
    bad_signed = SignedManifest(
        manifest=signed.manifest,
        signature=bad_block,
        public_key=public_key_to_base64(public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(bad_signed)
    msg = exc_info.value.message.lower()
    assert "tamper" in msg or "invalid" in msg or "malleability" in msg


def test_verify_manifest_wrong_public_key_raises() -> None:
    manifest = _sample_manifest()
    signer_key, _ = generate_keypair()
    _, other_public_key = generate_keypair()
    signed = sign_manifest(manifest, signer_key)
    signed_other_pk = SignedManifest(
        manifest=signed.manifest,
        signature=signed.signature,
        public_key=public_key_to_base64(other_public_key),
    )
    with pytest.raises(SignatureVerificationError) as exc_info:
        verify_manifest(signed_other_pk)
    assert "tamper" in exc_info.value.message.lower() or "invalid" in exc_info.value.message.lower()


# --- RFC 8032 Strict Verification (ADR-20) ---


class TestRFC8032StrictVerification:
    """RFC 8032 strict (s < l, canonical R, non-zero); ADR-20 malleability."""

    def test_reject_malleable_signature_s_ge_l(self) -> None:
        from asap.crypto.signing import ED25519_ORDER

        manifest = _sample_manifest()
        private_key, public_key = generate_keypair()
        signed = sign_manifest(manifest, private_key)

        raw_sig = base64.b64decode(signed.signature.signature)
        r_bytes = raw_sig[:32]
        s_int = int.from_bytes(raw_sig[32:], "little")
        s_malleable = s_int + ED25519_ORDER
        s_malleable_bytes = s_malleable.to_bytes(32, "little")
        malleable_sig = base64.b64encode(r_bytes + s_malleable_bytes).decode("ascii")

        bad_block = SignatureBlock.model_construct(alg="ed25519", signature=malleable_sig)
        bad_signed = SignedManifest(
            manifest=signed.manifest,
            signature=bad_block,
            public_key=public_key_to_base64(public_key),
        )
        with pytest.raises(SignatureVerificationError) as exc_info:
            verify_manifest(bad_signed)
        assert "malleability" in exc_info.value.message.lower()

    def test_reject_non_canonical_r_encoding(self) -> None:
        manifest = _sample_manifest()
        private_key, public_key = generate_keypair()
        signed = sign_manifest(manifest, private_key)

        raw_sig = bytearray(base64.b64decode(signed.signature.signature))
        raw_sig[31] ^= 0x80
        bad_sig_b64 = base64.b64encode(bytes(raw_sig)).decode("ascii")

        bad_block = SignatureBlock.model_construct(alg="ed25519", signature=bad_sig_b64)
        bad_signed = SignedManifest(
            manifest=signed.manifest,
            signature=bad_block,
            public_key=public_key_to_base64(public_key),
        )
        with pytest.raises(SignatureVerificationError):
            verify_manifest(bad_signed)

    def test_reject_all_zero_signature(self) -> None:
        manifest = _sample_manifest()
        private_key, public_key = generate_keypair()
        signed = sign_manifest(manifest, private_key)

        zero_sig_b64 = base64.b64encode(b"\x00" * 64).decode("ascii")
        bad_block = SignatureBlock.model_construct(alg="ed25519", signature=zero_sig_b64)
        bad_signed = SignedManifest(
            manifest=signed.manifest,
            signature=bad_block,
            public_key=public_key_to_base64(public_key),
        )
        with pytest.raises(SignatureVerificationError):
            verify_manifest(bad_signed)

    def test_valid_signature_s_below_l(self) -> None:
        from asap.crypto.signing import ED25519_ORDER

        manifest = _sample_manifest()
        private_key, _ = generate_keypair()
        signed = sign_manifest(manifest, private_key)

        raw_sig = base64.b64decode(signed.signature.signature)
        s_int = int.from_bytes(raw_sig[32:], "little")
        assert s_int < ED25519_ORDER
        assert verify_manifest(signed) is True

    def test_jcs_canonicalization_rfc8785_compliance(self) -> None:
        manifest = _sample_manifest()
        canonical = canonicalize(manifest)
        import json

        parsed = json.loads(canonical)
        keys = list(parsed.keys())
        assert keys == sorted(keys), "JCS must produce sorted top-level keys"

    def test_canonicalize_signature_field_excluded(self) -> None:
        manifest = _sample_manifest(signature="fake-sig-value")
        canonical = canonicalize(manifest)
        assert b'"signature"' not in canonical
