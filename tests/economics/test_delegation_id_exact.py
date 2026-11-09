"""Delegation revocation and issuer lookup must use the full id.

A prefix or case-insensitive match would revoke a different token, or walk
a child issued by a neighboring agent URN.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asap.economics.delegation_storage import (
    DelegationStorageBase,
    InMemoryDelegationStorage,
    SQLiteDelegationStorage,
)

_EXACT_TOKEN = "tok_01"
_EXACT_ISSUER = "urn:asap:agent:bot-01"
_EXACT_DELEGATE = "urn:asap:agent:worker-010"
_SHORT_DELEGATE = "urn:asap:agent:worker-01"
_CHILD_TOKEN = "tok_child"
_NEIGHBOR_CHILD = "tok_neighbor"
_NEIGHBOR_TOKENS: tuple[str, ...] = (
    "tok_010",
    "tok_0",
    "Tok_01",
    "tok_01 ",
    " tok_01",
)
_ISSUED: tuple[tuple[str, str, str], ...] = (
    (_EXACT_TOKEN, _EXACT_ISSUER, _EXACT_DELEGATE),
    ("tok_010", "urn:asap:agent:bot-010", "urn:asap:agent:worker-010"),
    ("tok_0", "urn:asap:agent:bot-0", "urn:asap:agent:worker-0"),
    ("Tok_01", "URN:asap:agent:bot-01", "urn:asap:agent:worker-case"),
    ("tok_01 ", "urn:asap:agent:bot-01 ", "urn:asap:agent:worker-trail"),
    (" tok_01", " urn:asap:agent:bot-01", "urn:asap:agent:worker-lead"),
    (_CHILD_TOKEN, _EXACT_DELEGATE, "urn:asap:agent:worker-child"),
    (_NEIGHBOR_CHILD, _SHORT_DELEGATE, "urn:asap:agent:worker-neighbor"),
)


@pytest.fixture(params=["memory", "sqlite"])
def delegation_storage(request: pytest.FixtureRequest, tmp_path: Path) -> DelegationStorageBase:
    """Fresh memory or SQLite delegation store."""
    if request.param == "memory":
        return InMemoryDelegationStorage()
    if request.param == "sqlite":
        return SQLiteDelegationStorage(db_path=tmp_path / "delegation-exact.db")
    raise AssertionError(f"unknown delegation storage {request.param!r}")


async def _register_neighbor_delegations(storage: DelegationStorageBase) -> None:
    """Insert the exact delegation beside prefix, case, and padded neighbors."""
    for token_id, issuer, delegate in _ISSUED:
        await storage.register_issued(token_id, issuer, delegate_urn=delegate)


def _assert_token_set(found: list[str], expected: set[str]) -> None:
    """Compare issuer results without depending on row order."""
    assert set(found) == expected


async def _assert_issuer_lists_are_exact(storage: DelegationStorageBase) -> None:
    """Issuer queries return only the token registered under that exact URN."""
    _assert_token_set(
        await storage.list_token_ids_issued_by(_EXACT_ISSUER),
        {_EXACT_TOKEN},
    )
    _assert_token_set(
        await storage.list_token_ids_issued_by("urn:asap:agent:bot-0"),
        {"tok_0"},
    )
    _assert_token_set(
        await storage.list_token_ids_issued_by(_EXACT_DELEGATE),
        {_CHILD_TOKEN},
    )
    _assert_token_set(
        await storage.list_token_ids_issued_by(_SHORT_DELEGATE),
        {_NEIGHBOR_CHILD},
    )


async def _assert_revoked_token_is_exact(storage: DelegationStorageBase) -> None:
    """Revoking ``tok_01`` does not revoke a prefix, case, or padded token."""
    assert await storage.is_revoked(_EXACT_TOKEN) is True
    flags = await storage.are_revoked([_EXACT_TOKEN, *_NEIGHBOR_TOKENS])
    assert flags[_EXACT_TOKEN] is True
    for token in _NEIGHBOR_TOKENS:
        assert await storage.is_revoked(token) is False
        assert flags[token] is False


async def _assert_cascade_stays_on_exact_delegate(storage: DelegationStorageBase) -> None:
    """Cascade follows the exact delegate URN, not a shorter issuer inside it."""
    assert await storage.is_revoked(_CHILD_TOKEN) is True
    assert await storage.is_revoked(_NEIGHBOR_CHILD) is False
    assert await storage.is_revoked("tok_010") is False


@pytest.mark.asyncio
async def test_delegation_token_and_issuer_match_are_exact(
    delegation_storage: DelegationStorageBase,
) -> None:
    """Issuer lists, revocation, and cascade children use the full ids."""
    await _register_neighbor_delegations(delegation_storage)
    await _assert_issuer_lists_are_exact(delegation_storage)
    await delegation_storage.revoke(_EXACT_TOKEN)
    await _assert_revoked_token_is_exact(delegation_storage)
    await delegation_storage.revoke_cascade(_EXACT_TOKEN)
    await _assert_cascade_stays_on_exact_delegate(delegation_storage)
