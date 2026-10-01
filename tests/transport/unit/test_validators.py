"""Unit tests for ASAP protocol envelope validators.

This module tests timestamp and nonce validation functions in isolation,
without HTTP dependencies or rate limiting.
"""

import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from asap.errors import InvalidNonceError, InvalidTimestampError
from asap.models.constants import MAX_ENVELOPE_AGE_SECONDS, NONCE_TTL_SECONDS
from asap.models.envelope import Envelope
from asap.transport.validators import (
    InMemoryNonceStore,
    validate_envelope_nonce,
    validate_envelope_timestamp,
)
from asap.transport import validators as validators_module


def _nonce_envelope(nonce: str) -> Envelope:
    """Build a task request envelope carrying ``nonce``."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:test",
        recipient="urn:asap:agent:test",
        payload_type="TaskRequest",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        extensions={"nonce": nonce},
    )


class TestTimestampValidation:
    """Tests for envelope timestamp validation."""

    def test_recent_timestamp_accepted(self) -> None:
        """Test that recent timestamps are accepted."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            timestamp=datetime.now(timezone.utc),
        )

        # Should not raise
        validate_envelope_timestamp(envelope)

    def test_old_timestamp_rejected(self) -> None:
        """Test that timestamps older than 5 minutes are rejected."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        with pytest.raises(InvalidTimestampError) as exc_info:
            validate_envelope_timestamp(envelope)

        assert "too old" in exc_info.value.message.lower()
        assert exc_info.value.age_seconds is not None
        assert exc_info.value.age_seconds > 300  # More than 5 minutes

    def test_future_timestamp_rejected(self) -> None:
        """Test that timestamps more than 30 seconds in the future are rejected."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            timestamp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        with pytest.raises(InvalidTimestampError) as exc_info:
            validate_envelope_timestamp(envelope)

        assert "too far in the future" in exc_info.value.message.lower()
        assert exc_info.value.future_offset_seconds is not None
        assert exc_info.value.future_offset_seconds > 30

    def test_timestamps_within_tolerance_accepted(self) -> None:
        """Test that timestamps within tolerance windows are accepted."""
        # Test timestamp 4 minutes old (within 5 minute window)
        envelope_old = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=4),
        )
        validate_envelope_timestamp(envelope_old)  # Should not raise

        # Test timestamp 20 seconds in the future (within 30 second tolerance)
        envelope_future = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            timestamp=datetime.now(timezone.utc) + timedelta(seconds=20),
        )
        validate_envelope_timestamp(envelope_future)  # Should not raise

    def test_auto_generated_timestamp_accepted(self) -> None:
        """Test that envelopes with auto-generated timestamps are accepted."""
        # Create envelope without explicit timestamp (will auto-generate)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )

        # Auto-generated timestamp should be recent and pass validation
        assert envelope.timestamp is not None
        validate_envelope_timestamp(envelope)  # Should not raise


class TestNonceValidation:
    """Tests for envelope nonce validation."""

    def test_no_nonce_passes(self) -> None:
        """Test that envelopes without nonce pass validation."""
        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )

        # Should not raise
        validate_envelope_nonce(envelope, store)

    def test_no_nonce_store_passes(self) -> None:
        """Test that validation passes when no nonce store is provided."""
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "test-nonce-123"},
        )

        # Should not raise when nonce_store is None
        validate_envelope_nonce(envelope, None)

    def test_first_nonce_use_passes(self) -> None:
        """Test that first use of a nonce passes and marks it as used."""
        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "unique-nonce-123"},
        )

        # First use should pass
        validate_envelope_nonce(envelope, store)

        # Verify nonce is marked as used: a second check_and_mark reports it
        # as already used (the race-safe way to confirm a nonce is spent).
        assert store.check_and_mark("unique-nonce-123", ttl_seconds=NONCE_TTL_SECONDS) is True

    def test_duplicate_nonce_rejected(self) -> None:
        """Test that duplicate nonces are rejected."""
        store = InMemoryNonceStore()
        envelope1 = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "duplicate-nonce-456"},
        )

        envelope2 = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "duplicate-nonce-456"},
        )

        # First use should pass
        validate_envelope_nonce(envelope1, store)

        # Second use should raise error
        with pytest.raises(InvalidNonceError) as exc_info:
            validate_envelope_nonce(envelope2, store)

        assert "duplicate nonce" in exc_info.value.message.lower()
        assert exc_info.value.nonce == "duplicate-nonce-456"

    def test_expired_nonce_allowed_again(self) -> None:
        """Test that expired nonces are allowed again after TTL expires."""
        import time

        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "expiring-nonce-789"},
        )

        # First use should pass
        validate_envelope_nonce(envelope, store)

        # Overwrite with a very short TTL (1 second). check_and_mark cannot
        # re-mark an already-used nonce, so set the expiry directly under the
        # store lock (same private-attribute pattern used by the TTL test below).
        current_time = time.time()
        with store._lock:
            store._store["expiring-nonce-789"] = current_time + 1

        # Use time mocking to simulate expiration without actual sleep
        with patch("asap.transport.validators.time.time") as mock_time:
            # First call: nonce still valid (within TTL) — check_and_mark
            # reports it as already used without re-marking.
            mock_time.return_value = current_time + 0.5
            assert store.check_and_mark("expiring-nonce-789", ttl_seconds=1) is True

            # Second call: nonce expired (past TTL)
            mock_time.return_value = current_time + 1.1
            # After expiration, should be allowed again
            validate_envelope_nonce(envelope, store)

    def test_invalid_nonce_type_raises_error(self) -> None:
        """Test that non-string nonces raise error."""
        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": 12345},  # Invalid: nonce should be string
        )

        with pytest.raises(InvalidNonceError) as exc_info:
            validate_envelope_nonce(envelope, store)

        assert "must be a non-empty string" in exc_info.value.message.lower()

    def test_empty_nonce_string_raises_error(self) -> None:
        """Test that empty string nonces raise error."""
        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": ""},  # Invalid: empty string
        )

        with pytest.raises(InvalidNonceError) as exc_info:
            validate_envelope_nonce(envelope, store)

        assert "must be a non-empty string" in exc_info.value.message.lower()
        assert "empty string" in exc_info.value.message.lower()

    def test_check_and_mark_returns_false_for_nonexistent_nonce(self) -> None:
        """check_and_mark returns False (newly marked) for a nonce not in the store."""
        store = InMemoryNonceStore()
        assert store.check_and_mark("nonexistent-nonce", ttl_seconds=NONCE_TTL_SECONDS) is False

    def test_nonce_ttl_uses_configured_constant(self) -> None:
        """Test that nonce TTL uses the configured constant (2x envelope age).

        This test verifies that the nonce TTL is derived from MAX_ENVELOPE_AGE_SECONDS
        as specified in NONCE_TTL_SECONDS. The 2x multiplier provides a safety margin
        to ensure nonces remain valid for the full envelope validation window while
        preventing unbounded growth of the nonce store.
        """
        import time

        store = InMemoryNonceStore()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:test",
            recipient="urn:asap:agent:test",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions={"nonce": "ttl-test-nonce"},
        )

        assert NONCE_TTL_SECONDS == MAX_ENVELOPE_AGE_SECONDS * 2

        validate_envelope_nonce(envelope, store)

        # Verify nonce is stored with correct TTL by checking expiration time
        current_time = time.time()
        with store._lock:
            assert "ttl-test-nonce" in store._store
            expiry_time = store._store["ttl-test-nonce"]
            # TTL should be approximately NONCE_TTL_SECONDS (allow small timing variance)
            actual_ttl = expiry_time - current_time
            assert abs(actual_ttl - NONCE_TTL_SECONDS) < 1.0  # Within 1 second tolerance

    def test_cleanup_probability_is_five_percent(self) -> None:
        """_CLEANUP_PROBABILITY is 0.05 to reduce memory drift under high throughput."""
        assert validators_module._CLEANUP_PROBABILITY == 0.05

    def test_cleanup_runs_when_store_exceeds_max_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When len(_store) > _MAX_NONCE_STORE_SIZE, cleanup runs and prunes expired."""
        monkeypatch.setattr(validators_module, "_MAX_NONCE_STORE_SIZE", 2)
        store = InMemoryNonceStore()
        # Add 3 nonces with expiry in the past (patch time so they expire immediately after).
        with patch("asap.transport.validators.time") as mock_time:
            mock_time.time.return_value = 0.0
            for i in range(3):
                store.check_and_mark(f"nonce-{i}", ttl_seconds=1)
            assert len(store._store) == 3
            mock_time.time.return_value = 2.0
            store.check_and_mark("new-nonce", ttl_seconds=10)
        assert len(store._store) == 1
        assert "new-nonce" in store._store

    def test_concurrent_same_nonce_accepted_once(self) -> None:
        """Parallel checks of one nonce accept exactly one caller.

        ``check_and_mark`` is the replay lock. A regression that splits the
        check from the mark would let two state-changing envelopes through.
        """
        store = InMemoryNonceStore()
        worker_count = 16
        barrier = threading.Barrier(worker_count)
        outcomes: list[str] = []
        outcomes_lock = threading.Lock()

        def validate_once() -> None:
            envelope = _nonce_envelope("shared-race-nonce")
            barrier.wait()
            try:
                validate_envelope_nonce(envelope, store)
            except InvalidNonceError:
                outcome = "duplicate"
            except Exception as exc:
                outcome = f"error:{type(exc).__name__}:{exc}"
            else:
                outcome = "accepted"
            with outcomes_lock:
                outcomes.append(outcome)

        workers = [threading.Thread(target=validate_once) for _ in range(worker_count)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        assert outcomes.count("accepted") == 1
        assert outcomes.count("duplicate") == worker_count - 1
