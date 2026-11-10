"""Unit tests for Ed25519 key generation, serialization, and loading."""

import base64
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from asap.crypto.keys import (
    KeyMetadata,
    generate_keypair,
    get_key_metadata_from_file,
    load_private_key_from_env,
    load_private_key_from_file_sync,
    load_private_key_from_pem,
    load_public_key_from_base64,
    public_key_to_base64,
    serialize_private_key,
    warn_if_key_file_permissions_loose,
    warn_if_key_old,
)


def test_generate_keypair_returns_keypair() -> None:
    """generate_keypair returns (Ed25519PrivateKey, Ed25519PublicKey)."""
    private_key, public_key = generate_keypair()
    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, Ed25519PublicKey)
    assert public_key == private_key.public_key()


def test_generate_keypair_can_sign_and_verify() -> None:
    """Generated key can sign a message and verify the signature."""
    private_key, public_key = generate_keypair()
    message = b"test message"
    signature = private_key.sign(message)
    public_key.verify(signature, message)
    assert len(signature) == 64


def test_serialize_private_key_returns_pem_bytes() -> None:
    """serialize_private_key returns PEM-encoded bytes."""
    private_key, _ = generate_keypair()
    pem = serialize_private_key(private_key)
    assert isinstance(pem, bytes)
    assert b"-----BEGIN" in pem and b"PRIVATE KEY" in pem


def test_roundtrip_serialize_deserialize_compare() -> None:
    """Roundtrip: serialize -> load_private_key_from_pem -> same signature."""
    private_key, public_key = generate_keypair()
    pem = serialize_private_key(private_key)
    loaded = load_private_key_from_pem(pem)
    message = b"roundtrip"
    assert loaded.sign(message) == private_key.sign(message)
    assert public_key_to_base64(loaded.public_key()) == public_key_to_base64(public_key)


def test_public_key_to_base64_returns_str() -> None:
    """public_key_to_base64 returns a base64 string (32 bytes raw = 44 chars)."""
    _, public_key = generate_keypair()
    encoded = public_key_to_base64(public_key)
    assert isinstance(encoded, str)
    assert len(encoded) > 0
    # Raw Ed25519 is 32 bytes -> base64 without padding is 43 chars, with padding 44
    assert 40 <= len(encoded) <= 44


def test_public_key_to_base64_deterministic() -> None:
    """Same key produces same base64 output."""
    _, public_key = generate_keypair()
    assert public_key_to_base64(public_key) == public_key_to_base64(public_key)


def test_load_private_key_from_pem_invalid_raises() -> None:
    """load_private_key_from_pem raises ValueError for invalid PEM."""
    with pytest.raises(ValueError):
        load_private_key_from_pem(b"not valid pem")


def test_load_private_key_from_pem_non_ed25519_raises() -> None:
    """load_private_key_from_pem raises ValueError for non-Ed25519 key."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    with pytest.raises(ValueError, match="not an Ed25519 private key"):
        load_private_key_from_pem(pem)


def test_load_private_key_from_file_success(tmp_path: Path) -> None:
    """load_private_key_from_file_sync loads key from a PEM file."""
    private_key, _ = generate_keypair()
    pem = serialize_private_key(private_key)
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(pem)
    key_file.chmod(0o600)
    with patch("asap.crypto.keys.logger") as mock_logger:
        loaded = load_private_key_from_file_sync(key_file)
    assert loaded.sign(b"x") == private_key.sign(b"x")
    mock_logger.warning.assert_not_called()


def test_load_private_key_from_file_accepts_str_path(tmp_path: Path) -> None:
    """load_private_key_from_file_sync accepts string path."""
    private_key, _ = generate_keypair()
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(serialize_private_key(private_key))
    loaded = load_private_key_from_file_sync(str(key_file))
    assert loaded.sign(b"y") == private_key.sign(b"y")


def test_load_private_key_from_file_missing_raises() -> None:
    """load_private_key_from_file_sync raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        load_private_key_from_file_sync("/nonexistent/key.pem")


@pytest.mark.parametrize(
    ("mode", "warns"),
    [
        (0o600, False),
        (0o640, True),
        (0o604, True),
    ],
)
def test_key_file_permission_warning_uses_group_and_other_bits(
    tmp_path: Path,
    mode: int,
    warns: bool,
) -> None:
    """Owner-only mode 0600 stays quiet. Group-read and other-read each warn.

    Group-read 0640 and other-read 0604 each log ``key_file_permissions_loose``.
    Those two modes split the group bits from the other bits.
    """
    key_file = tmp_path / f"key-{mode:o}.pem"
    key_file.write_bytes(b"not-a-real-key")
    key_file.chmod(mode)
    assert key_file.stat().st_mode & 0o777 == mode

    with patch("asap.crypto.keys.logger") as mock_logger:
        warn_if_key_file_permissions_loose(key_file)

    if warns:
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "key_file_permissions_loose"
        return
    mock_logger.warning.assert_not_called()


def test_key_file_permission_warning_ignores_missing_path(tmp_path: Path) -> None:
    """A missing path does not raise and does not log a permission warning."""
    with patch("asap.crypto.keys.logger") as mock_logger:
        warn_if_key_file_permissions_loose(tmp_path / "missing.pem")
    mock_logger.warning.assert_not_called()


def test_load_private_key_from_file_logs_warning_when_permissions_loose(
    tmp_path: Path,
) -> None:
    """Loading a key file readable by group/others logs a security warning."""
    private_key, _ = generate_keypair()
    key_file = tmp_path / "loose.pem"
    key_file.write_bytes(serialize_private_key(private_key))
    key_file.chmod(0o644)
    with patch("asap.crypto.keys.logger") as mock_logger:
        load_private_key_from_file_sync(key_file)
    mock_logger.warning.assert_called()
    calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("key_file_permissions_loose" in c or "permissions" in c for c in calls)


def test_load_private_key_from_env_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_private_key_from_env loads key from environment variable."""
    private_key, _ = generate_keypair()
    pem = serialize_private_key(private_key)
    monkeypatch.setenv("ASAP_TEST_PRIVATE_KEY", pem.decode("utf-8"))
    loaded = load_private_key_from_env("ASAP_TEST_PRIVATE_KEY")
    assert loaded.sign(b"z") == private_key.sign(b"z")
    monkeypatch.delenv("ASAP_TEST_PRIVATE_KEY", raising=False)


def test_load_private_key_from_env_unset_raises() -> None:
    """load_private_key_from_env raises ValueError when variable is unset."""
    # Ensure not set
    os.environ.pop("ASAP_NONEXISTENT_KEY_VAR", None)
    with pytest.raises(ValueError, match="is not set or empty"):
        load_private_key_from_env("ASAP_NONEXISTENT_KEY_VAR")


def test_load_private_key_from_env_empty_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_private_key_from_env raises ValueError when variable is empty."""
    monkeypatch.setenv("ASAP_EMPTY_KEY", "")
    with pytest.raises(ValueError, match="is not set or empty"):
        load_private_key_from_env("ASAP_EMPTY_KEY")
    monkeypatch.delenv("ASAP_EMPTY_KEY", raising=False)


def test_key_metadata_model() -> None:
    """KeyMetadata accepts datetime and serializes."""
    from datetime import datetime, timezone

    created = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    meta = KeyMetadata(created_at=created)
    assert meta.created_at == created


def test_get_key_metadata_from_file_uses_mtime(tmp_path: Path) -> None:
    """get_key_metadata_from_file sets created_at from file mtime."""
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(b"-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----\n")
    # Set mtime to a known time
    t = 1705312800.0  # 2024-01-15 12:00:00 UTC
    os.utime(key_file, (t, t))
    meta = get_key_metadata_from_file(key_file)
    assert meta.created_at.timestamp() == pytest.approx(t, abs=1)


def test_warn_if_key_old_logs_when_old() -> None:
    """warn_if_key_old logs warning when key age >= max_age_days."""
    from datetime import datetime, timezone

    old_date = datetime.now(timezone.utc).replace(year=2023, month=1, day=1)
    meta = KeyMetadata(created_at=old_date)
    with patch("asap.crypto.keys.logger") as mock_logger:
        warn_if_key_old(meta, max_age_days=365)
    mock_logger.warning.assert_called_once()
    call_kw = mock_logger.warning.call_args[1]
    assert call_kw.get("age_days", 0) >= 365
    assert call_kw.get("max_age_days") == 365
    assert "key_rotation" in str(mock_logger.warning.call_args[0])


def test_warn_if_key_old_no_log_when_recent() -> None:
    """warn_if_key_old does not log when key is younger than max_age_days."""
    from datetime import datetime, timezone

    recent = datetime.now(timezone.utc)
    meta = KeyMetadata(created_at=recent)
    with patch("asap.crypto.keys.logger") as mock_logger:
        warn_if_key_old(meta, max_age_days=365)
    mock_logger.warning.assert_not_called()


def test_load_private_key_from_file_logs_warning_for_old_key(
    tmp_path: Path,
) -> None:
    """Loading a key file older than KEY_ROTATION_WARNING_DAYS logs a warning."""
    private_key, _ = generate_keypair()
    key_file = tmp_path / "old.pem"
    key_file.write_bytes(serialize_private_key(private_key))
    key_file.chmod(0o600)
    old_ts = time.time() - (400 * 24 * 3600)
    os.utime(key_file, (old_ts, old_ts))
    with patch("asap.crypto.keys.logger") as mock_logger:
        load_private_key_from_file_sync(key_file)
    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args
    assert "key_rotation_recommended" in str(call_args)


def test_load_public_key_from_base64_rejects_non_32_byte_payloads() -> None:
    """Manifest trust loading accepts 32 raw bytes and rejects one byte either side.

    A length check that uses ``>`` or ``<`` would accept a 33-byte or 31-byte
    payload and hand a wrong key to signature verification.
    """
    _private_key, public_key = generate_keypair()
    encoded = public_key_to_base64(public_key)
    loaded = load_public_key_from_base64(encoded)
    assert public_key_to_base64(loaded) == encoded

    raw = base64.b64decode(encoded)
    short_key = base64.b64encode(raw[:-1]).decode("ascii")
    long_key = base64.b64encode(raw + b"\x00").decode("ascii")
    with pytest.raises(ValueError, match="32 bytes, got 31"):
        load_public_key_from_base64(short_key)
    with pytest.raises(ValueError, match="32 bytes, got 33"):
        load_public_key_from_base64(long_key)
