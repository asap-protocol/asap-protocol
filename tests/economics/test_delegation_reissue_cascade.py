"""Re-registering a token id replaces the delegate used by cascade revoke.

``INSERT OR REPLACE`` and the in-memory dict assign must retarget the graph.
Keeping the first delegate revokes the wrong subtree.
"""

from __future__ import annotations

from pathlib import Path

from asap.economics.delegation_storage import (
    DelegationStorage,
    InMemoryDelegationStorage,
    SQLiteDelegationStorage,
)

_ROOT = "del_root"
_DELEGATOR = "urn:asap:agent:A"
_OLD_DELEGATE = "urn:asap:agent:B"
_NEW_DELEGATE = "urn:asap:agent:C"


async def _assert_reissue_retargets_cascade(storage: DelegationStorage) -> None:
    """Second registration of the same id follows the new delegate only."""
    await storage.register_issued(_ROOT, _DELEGATOR, delegate_urn=_OLD_DELEGATE)
    await storage.register_issued(
        "child-b",
        _OLD_DELEGATE,
        delegate_urn="urn:asap:agent:B2",
    )
    await storage.register_issued(_ROOT, _DELEGATOR, delegate_urn=_NEW_DELEGATE)
    await storage.register_issued(
        "child-c",
        _NEW_DELEGATE,
        delegate_urn="urn:asap:agent:C2",
    )

    assert await storage.get_delegate(_ROOT) == _NEW_DELEGATE
    detail = await storage.get_token_detail(_ROOT)
    assert detail is not None
    assert detail.delegate_urn == _NEW_DELEGATE

    await storage.revoke_cascade(_ROOT, reason="reissued")
    assert await storage.is_revoked(_ROOT) is True
    assert await storage.is_revoked("child-c") is True
    assert await storage.is_revoked("child-b") is False


async def test_memory_reissue_retargets_cascade() -> None:
    """In-memory registration overwrites the delegate before cascade."""
    await _assert_reissue_retargets_cascade(InMemoryDelegationStorage())


async def test_sqlite_reissue_retargets_cascade(tmp_path: Path) -> None:
    """SQLite ``INSERT OR REPLACE`` overwrites the delegate before cascade."""
    storage = SQLiteDelegationStorage(db_path=tmp_path / "delegations.db")
    await _assert_reissue_retargets_cascade(storage)
