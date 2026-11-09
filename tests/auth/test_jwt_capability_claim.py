"""Typed view of the Agent JWT capabilities claim."""

from __future__ import annotations

from asap.auth.agent_jwt import JwtVerifyResult


def test_jwt_capabilities_claim_is_a_list_of_strings() -> None:
    """Only string entries in a list count as capability names.

    A dict claim must not become its keys. A string claim must not become
    its characters. Non-string list entries are dropped.
    """
    listed = JwtVerifyResult(
        ok=True,
        claims={"capabilities": ["web_search", "file:read"]},
    )
    assert listed.capabilities == ["web_search", "file:read"]

    for raw in (
        {"file:read": True, "file:write": True},
        "file:read",
        True,
        1,
        None,
    ):
        result = JwtVerifyResult(ok=True, claims={"capabilities": raw})
        assert result.capabilities == []

    mixed = JwtVerifyResult(
        ok=True,
        claims={"capabilities": ["file:read", 99, True, None, "file:write"]},
    )
    assert mixed.capabilities == ["file:read", "file:write"]

    assert JwtVerifyResult(ok=True, claims={}).capabilities == []
    assert JwtVerifyResult(ok=True).capabilities == []
