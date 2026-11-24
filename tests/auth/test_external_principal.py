"""External principal and typed errors."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from asap.auth.capabilities import CapabilityGrant
from asap.auth.external_tokens import (
    ExternalPrincipal,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.identity import AgentSession


def _principal() -> ExternalPrincipal:
    return ExternalPrincipal(
        issuer="https://issuer.example",
        subject="user-42",
        client_id="mcp-client",
        scopes=("files:read",),
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        grants=(CapabilityGrant(capability="file.read", status="active"),),
    )


def test_principal_is_keyed_by_issuer_and_subject() -> None:
    assert _principal().key == ("https://issuer.example", "user-42")


def test_principal_is_frozen_and_is_not_an_agent_session() -> None:
    principal = _principal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.subject = "someone-else"
    assert not isinstance(principal, AgentSession)
    assert not hasattr(principal, "agent_id")


def test_error_carries_a_typed_reason_and_a_stable_code() -> None:
    error = ExternalTokenError(ExternalTokenErrorReason.AUDIENCE_MISMATCH, "aud is not ours")
    assert error.reason is ExternalTokenErrorReason.AUDIENCE_MISMATCH
    assert str(error) == "audience_mismatch: aud is not ours"


def test_reason_codes_are_stable_strings() -> None:
    assert [reason.value for reason in ExternalTokenErrorReason] == [
        "malformed_token",
        "token_type_rejected",
        "untrusted_issuer",
        "algorithm_rejected",
        "invalid_signature",
        "audience_mismatch",
        "token_expired",
        "token_not_yet_valid",
        "missing_claim",
        "token_inactive",
        "key_source_unavailable",
    ]
