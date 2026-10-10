"""Tests for self-authorization prevention (fresh session, WebAuthn, CIBA preference)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from asap.auth.approval import select_approval_method
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    HostStatus,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
)
from asap.auth.self_auth import (
    FreshSessionConfig,
    PlaceholderWebAuthnVerifier,
    WebAuthnVerifier,
    check_fresh_session,
    check_webauthn_for_approval_path,
    default_webauthn_verifier,
    fresh_session_violation_detail,
    host_jwt_issued_at_seconds,
    uses_real_webauthn_verifier,
    verify_webauthn_if_required,
    webauthn_required_capability_names,
)
from tests.crypto.jwk_helpers import make_ed25519_jwk


def _sample_host(
    *,
    user_id: str | None = None,
    status: HostStatus = "pending",
) -> HostIdentity:
    now = datetime.now(timezone.utc)
    pk = make_ed25519_jwk()
    hid = host_urn_from_thumbprint(jwk_thumbprint_sha256(pk))
    return HostIdentity(
        host_id=hid,
        public_key=pk,
        user_id=user_id,
        status=status,
        default_capabilities=[],
        created_at=now,
        updated_at=now,
    )


def _sample_agent() -> AgentSession:
    now = datetime.now(timezone.utc)
    pk = make_ed25519_jwk()
    return AgentSession(
        agent_id="agent-x",
        host_id="host-y",
        public_key=pk,
        mode="delegated",
        status="pending",
        created_at=now,
    )


def test_check_fresh_session_within_window() -> None:
    cfg = FreshSessionConfig(window_seconds=300)
    now = 1000.0
    assert check_fresh_session(950.0, cfg, now_ts=now) is True


def test_host_jwt_issued_at_invalid() -> None:
    assert host_jwt_issued_at_seconds({"iat": "not-a-number"}) is None


def test_check_fresh_session_uses_system_time_when_now_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("asap.auth.self_auth.time.time", lambda: 2000.0)
    cfg = FreshSessionConfig(window_seconds=100)
    assert check_fresh_session(1950.0, cfg) is True


def test_webauthn_required_capability_names_order() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["b", "a"])
    assert webauthn_required_capability_names(["a", "c", "b"], cfg) == ["a", "b"]


def test_check_fresh_session_outside_window() -> None:
    cfg = FreshSessionConfig(window_seconds=300)
    now = 1000.0
    assert check_fresh_session(600.0, cfg, now_ts=now) is False


def test_fresh_session_violation_missing_iat() -> None:
    cfg = FreshSessionConfig()
    detail = fresh_session_violation_detail({}, cfg, now_ts=0.0)
    assert detail is not None


def test_fresh_session_violation_stale() -> None:
    cfg = FreshSessionConfig(window_seconds=60)
    detail = fresh_session_violation_detail({"iat": 100}, cfg, now_ts=200.0)
    assert detail is not None
    assert "stale" in detail


def test_fresh_session_ok() -> None:
    cfg = FreshSessionConfig(window_seconds=300)
    assert fresh_session_violation_detail({"iat": 1000}, cfg, now_ts=1005.0) is None


def test_webauthn_protocol_runtime_checkable() -> None:
    v = PlaceholderWebAuthnVerifier()
    assert isinstance(v, WebAuthnVerifier)


@pytest.mark.asyncio
async def test_placeholder_webauthn_verifier_accepts_anything() -> None:
    v = PlaceholderWebAuthnVerifier()
    assert await v.verify("ch", {"x": 1}) is True


@pytest.mark.asyncio
async def test_placeholder_webauthn_verifier_logs_warning_once() -> None:
    PlaceholderWebAuthnVerifier._warned = False
    mock_log = MagicMock()
    with patch("asap.auth.self_auth.get_logger", return_value=mock_log):
        v = PlaceholderWebAuthnVerifier()
        assert await v.verify("c1", {}) is True
        mock_log.warning.assert_called_once()
        assert await v.verify("c2", {}) is True
        assert mock_log.warning.call_count == 1


@pytest.mark.asyncio
async def test_verify_webauthn_skips_when_no_overlap() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    v = PlaceholderWebAuthnVerifier()
    err = await verify_webauthn_if_required(["read"], {}, cfg, v)
    assert err is None


@pytest.mark.asyncio
async def test_verify_webauthn_requires_block() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    v = PlaceholderWebAuthnVerifier()
    err = await verify_webauthn_if_required(["admin.task"], {}, cfg, v)
    assert err is not None


@pytest.mark.asyncio
async def test_verify_webauthn_rejects_blank_challenge() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    v = PlaceholderWebAuthnVerifier()
    body: dict[str, Any] = {"webauthn": {"challenge": "", "response": {}}}
    err = await verify_webauthn_if_required(["admin.task"], body, cfg, v)
    assert err is not None


@pytest.mark.asyncio
async def test_verify_webauthn_rejects_missing_response() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    v = PlaceholderWebAuthnVerifier()
    body: dict[str, Any] = {"webauthn": {"challenge": "ok"}}
    err = await verify_webauthn_if_required(["admin.task"], body, cfg, v)
    assert err is not None


@pytest.mark.asyncio
async def test_verify_webauthn_success_with_placeholder() -> None:
    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    v = PlaceholderWebAuthnVerifier()
    body: dict[str, Any] = {"webauthn": {"challenge": "c1", "response": {}}}
    err = await verify_webauthn_if_required(["admin.task"], body, cfg, v, host_id="urn:asap:host:x")
    assert err is None


@pytest.mark.asyncio
async def test_verify_webauthn_failure_when_verifier_rejects() -> None:
    class _Reject(WebAuthnVerifier):
        async def verify(
            self,
            _challenge: str,
            _response: Any,
            *,
            host_id: str | None = None,
            require_user_verification: bool = False,
        ) -> bool:
            return False

    cfg = FreshSessionConfig(require_webauthn_for=["admin.task"])
    body: dict[str, Any] = {"webauthn": {"challenge": "c1", "response": {}}}
    err = await verify_webauthn_if_required(
        ["admin.task"], body, cfg, _Reject(), host_id="urn:asap:host:x"
    )
    assert err is not None


@pytest.mark.asyncio
async def test_check_webauthn_browser_gate_returns_403_without_payload() -> None:
    class _Real(WebAuthnVerifier):
        __asap_performs_real_webauthn__ = True

        async def verify(
            self,
            _c: str,
            _r: Any,
            *,
            host_id: str | None = None,
            require_user_verification: bool = False,
        ) -> bool:
            return True

    v = _Real()
    assert uses_real_webauthn_verifier(v) is True
    r = await check_webauthn_for_approval_path(
        [],
        {},
        None,
        v,
        host_id="urn:asap:host:1",
        agent_controls_browser=True,
    )
    assert r.detail is not None
    assert r.http_status == 403


def _recording_real_verifier(seen: list[bool]) -> WebAuthnVerifier:
    """Verifier that records whether the approval path demanded user verification."""

    class _RealVerifier(WebAuthnVerifier):
        __asap_performs_real_webauthn__ = True

        async def verify(
            self,
            _challenge: str,
            _response: Any,
            *,
            host_id: str | None = None,
            require_user_verification: bool = False,
        ) -> bool:
            seen.append(require_user_verification)
            _ = host_id
            return True

    return _RealVerifier()


@pytest.mark.asyncio
async def test_browser_controlled_approval_requires_user_verification() -> None:
    """A real verifier on a browser-controlled agent must be asked for user verification."""
    seen: list[bool] = []
    result = await check_webauthn_for_approval_path(
        [],
        {"webauthn": {"challenge": "c1", "response": {"id": "cred"}}},
        None,
        _recording_real_verifier(seen),
        host_id="urn:asap:host:1",
        agent_controls_browser=True,
    )
    assert result.detail is None
    assert seen == [True]


@pytest.mark.asyncio
async def test_capability_webauthn_does_not_require_user_verification() -> None:
    """High-risk capabilities alone do not force user verification."""
    seen: list[bool] = []
    result = await check_webauthn_for_approval_path(
        ["admin.task"],
        {"webauthn": {"challenge": "c1", "response": {"id": "cred"}}},
        FreshSessionConfig(require_webauthn_for=["admin.task"]),
        _recording_real_verifier(seen),
        host_id="urn:asap:host:1",
        agent_controls_browser=False,
    )
    assert result.detail is None
    assert seen == [False]


def test_placeholder_not_real_webauthn() -> None:
    assert uses_real_webauthn_verifier(PlaceholderWebAuthnVerifier()) is False


def test_default_webauthn_verifier_is_placeholder_without_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("asap.auth.self_auth._webauthn_extra_installed", lambda: False)
    v = default_webauthn_verifier()
    assert isinstance(v, PlaceholderWebAuthnVerifier)


def test_default_webauthn_verifier_is_placeholder_when_rp_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("asap.auth.self_auth._webauthn_extra_installed", lambda: True)
    monkeypatch.delenv("ASAP_WEBAUTHN_RP_ID", raising=False)
    monkeypatch.delenv("ASAP_WEBAUTHN_ORIGIN", raising=False)
    v = default_webauthn_verifier()
    assert isinstance(v, PlaceholderWebAuthnVerifier)


def test_default_webauthn_verifier_real_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    if importlib.util.find_spec("webauthn") is None:
        pytest.skip("webauthn extra not installed in this environment")
    monkeypatch.setattr("asap.auth.self_auth._webauthn_extra_installed", lambda: True)
    monkeypatch.setenv("ASAP_WEBAUTHN_RP_ID", "localhost")
    monkeypatch.setenv("ASAP_WEBAUTHN_ORIGIN", "http://localhost:8000")
    from asap.auth.webauthn import WebAuthnVerifierImpl

    v = default_webauthn_verifier()
    assert isinstance(v, WebAuthnVerifierImpl)
    assert isinstance(v, WebAuthnVerifier)
    assert uses_real_webauthn_verifier(v) is True


def test_select_browser_controls_forces_ciba_despite_device_hint() -> None:
    """Mitigation: browser-controlling clients should not complete Device Auth on same device."""
    h = _sample_host(user_id="u1", status="active")
    assert (
        select_approval_method(
            h,
            _sample_agent(),
            host_supports_ciba=True,
            preferred_method="device_authorization",
            agent_controls_browser=True,
        )
        == "ciba"
    )


def test_select_browser_controls_device_when_unlinked() -> None:
    h = _sample_host(user_id=None, status="active")
    assert (
        select_approval_method(
            h,
            _sample_agent(),
            host_supports_ciba=True,
            preferred_method="device_authorization",
            agent_controls_browser=True,
        )
        == "device_authorization"
    )
