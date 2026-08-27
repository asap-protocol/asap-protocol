"""AgentStore.save must atomically refuse to resurrect a revoked row.

The invariant lives on ``save`` (same class as ``NonceStore.check_and_mark``),
not on a get-then-save helper. HTTP paths that mutate then persist a full row
rely on this store-level guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from asap.auth.identity import (
    AgentSession,
    InMemoryAgentStore,
    RevokedAgentOverwriteError,
    raise_if_revoked_agent_overwrite,
    save_agent_unless_revoked,
)
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _active_session(agent_id: str = "a1") -> AgentSession:
    return AgentSession(
        agent_id=agent_id,
        host_id="h1",
        public_key=make_ed25519_jwk(),
        mode="delegated",
        status="active",
        created_at=_utc_now(),
    )


class _CountingGetAgentStore(InMemoryAgentStore):
    """Counts ``get`` calls so tests can prove the helper does not re-read."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    async def get(self, agent_id: str) -> AgentSession | None:
        self.get_calls += 1
        return await super().get(agent_id)


async def test_in_memory_agent_store_refuses_to_resurrect_revoked() -> None:
    """Stale full-row save must not overwrite a concurrent revoke."""
    store = InMemoryAgentStore()
    await store.save(_active_session())
    stale = await store.get("a1")
    assert stale is not None
    await store.revoke("a1")
    resurrected = stale.model_copy(update={"public_key": make_ed25519_jwk()})
    with pytest.raises(RevokedAgentOverwriteError, match="refusing to overwrite revoked"):
        await store.save(resurrected)
    row = await store.get("a1")
    assert row is not None and row.status == "revoked"


async def test_save_agent_unless_revoked_rejects_stale_active_snapshot() -> None:
    """Named persist helper delegates to save(); overwrite still raises."""
    store = InMemoryAgentStore()
    await store.save(_active_session())
    stale = await store.get("a1")
    assert stale is not None
    await store.revoke("a1")
    with pytest.raises(RevokedAgentOverwriteError, match="refusing to overwrite revoked"):
        await save_agent_unless_revoked(store, stale.model_copy(update={"status": "active"}))
    row = await store.get("a1")
    assert row is not None and row.status == "revoked"


async def test_save_agent_unless_revoked_allows_revoked_noop_update() -> None:
    """Persisting an already-revoked snapshot remains allowed."""
    store = InMemoryAgentStore()
    await store.save(_active_session())
    await store.revoke("a1")
    row = await store.get("a1")
    assert row is not None
    await save_agent_unless_revoked(store, row)
    stored = await store.get("a1")
    assert stored is not None and stored.status == "revoked"


async def test_save_agent_unless_revoked_does_not_re_get() -> None:
    """Helper must not add a TOCTOU get; the atomic guard is save() itself."""
    store = _CountingGetAgentStore()
    session = _active_session()
    await store.save(session)
    store.get_calls = 0
    await save_agent_unless_revoked(store, session)
    assert store.get_calls == 0


def test_raise_if_revoked_agent_overwrite_rejects_non_revoked_snapshot() -> None:
    """Shared guard used by custom AgentStore.save implementations."""
    revoked = _active_session().model_copy(update={"status": "revoked"})
    incoming = revoked.model_copy(update={"status": "active"})
    with pytest.raises(RevokedAgentOverwriteError) as exc_info:
        raise_if_revoked_agent_overwrite(revoked, incoming)
    assert exc_info.value.agent_id == "a1"
    assert exc_info.value.attempted_status == "active"


def test_raise_if_revoked_agent_overwrite_allows_missing_or_revoked_write() -> None:
    """Inserts and revoked-to-revoked updates are not resurrection."""
    incoming = _active_session()
    raise_if_revoked_agent_overwrite(None, incoming)
    revoked = incoming.model_copy(update={"status": "revoked"})
    raise_if_revoked_agent_overwrite(revoked, revoked)


async def test_touch_if_current_slides_last_used_at_without_clobbering_key() -> None:
    """Atomic touch updates only last_used_at when status, host, and key match."""
    store = InMemoryAgentStore()
    now = _utc_now()
    public_key = make_ed25519_jwk()
    await store.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=public_key,
            mode="delegated",
            status="active",
            created_at=now,
            last_used_at=now - timedelta(minutes=5),
        )
    )
    touched_at = now + timedelta(seconds=1)
    updated = await store.touch_if_current(
        "a1",
        {**public_key, "kid": "optional-member"},
        touched_at,
        expected_host_id="h1",
    )
    assert updated is not None
    assert updated.last_used_at == touched_at
    assert updated.status == "active"
    assert updated.public_key == public_key
    stored = await store.get("a1")
    assert stored == updated


async def test_touch_if_current_refuses_revoked_rotated_or_rehosted_row() -> None:
    """Predicate fails closed so a later save cannot resurrect or undo rotate."""
    store = InMemoryAgentStore()
    now = _utc_now()
    public_key = make_ed25519_jwk()
    revoked_row = AgentSession(
        agent_id="revoked-1",
        host_id="h1",
        public_key=public_key,
        mode="delegated",
        status="active",
        created_at=now,
    )
    await store.save(revoked_row)
    await store.revoke("revoked-1")
    assert (
        await store.touch_if_current("revoked-1", public_key, now, expected_host_id="h1")
    ) is None
    with pytest.raises(RevokedAgentOverwriteError):
        await store.save(revoked_row)

    rotated_row = AgentSession(
        agent_id="rotated-1",
        host_id="h1",
        public_key=public_key,
        mode="delegated",
        status="active",
        created_at=now,
    )
    await store.save(rotated_row)
    rotated = make_ed25519_jwk()
    await store.save(rotated_row.model_copy(update={"public_key": rotated}))
    assert (
        await store.touch_if_current("rotated-1", public_key, now, expected_host_id="h1")
    ) is None
    kept = await store.get("rotated-1")
    assert kept is not None and kept.public_key == rotated

    rehosted_row = AgentSession(
        agent_id="rehosted-1",
        host_id="h1",
        public_key=public_key,
        mode="delegated",
        status="active",
        created_at=now,
    )
    await store.save(rehosted_row)
    await store.save(rehosted_row.model_copy(update={"host_id": "other-host"}))
    assert (
        await store.touch_if_current("rehosted-1", public_key, now, expected_host_id="h1")
    ) is None
    rehosted = await store.get("rehosted-1")
    assert rehosted is not None and rehosted.host_id == "other-host"
