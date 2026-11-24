"""Tests for the ``a2a-bridge`` profile."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from joserfc.jwk import OKPKey
from pydantic import ValidationError

from asap_compliance.config import A2A_BRIDGE_PROFILE, A2ABridgeComplianceConfig
from asap_compliance.validators.a2a_bridge import (
    A2A_BRIDGE_CHECK_IDS,
    A2ABridgeResult,
    validate_a2a_bridge,
    validate_a2a_bridge_async,
)

from .a2a_fake_gateway import (
    AGENT_URN,
    BASE_URL,
    CARD_KID,
    CARD_PUBLIC_JWK,
    PROBE_TOKENS_PATH,
    OTHER_AGENT_URN,
    FakeA2AGateway,
    Fault,
)

_AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
_CARD_KEY = OKPKey.generate_key("Ed25519", private=True)
_PUBLIC_JWK = {**_CARD_KEY.as_dict(private=False), "kid": "card-1"}


def _config_fields(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "base_url": "https://gateway.example",
        "expected_agent_urn": _AGENT_URN,
        "card_keys": {"card-1": _PUBLIC_JWK},
    }
    return {**base, **fields}


class TestA2ABridgeConfig:
    def test_profile_name_and_example_defaults(self) -> None:
        config = A2ABridgeComplianceConfig(**_config_fields())

        assert config.profile == A2A_BRIDGE_PROFILE == "a2a-bridge"
        assert (config.skill_id, config.allow_private_targets) == ("greet", False)
        assert config.allowed_arguments == {"name": "Ada", "language": "en"}
        assert config.constraint_violation_arguments == {"name": "Ada", "language": "fr"}
        assert (config.timeout_seconds, config.skip_checks) == (10.0, [])

    def test_each_configured_key_is_pinned_for_the_expected_urn_only(self) -> None:
        config = A2ABridgeComplianceConfig(**_config_fields())

        trust = config.card_trust()

        assert {kid: entry.agents for kid, entry in trust.pinned_keys.items()} == {
            "card-1": (_AGENT_URN,)
        }
        assert trust.jku_allowlist == {}

    @pytest.mark.parametrize(
        "base_url", ["http://127.0.0.1:8788", "https://127.0.0.1:8788", "https://localhost"]
    )
    def test_a_local_target_needs_the_switch(self, base_url: str) -> None:
        with pytest.raises(ValidationError, match="allow_private_targets"):
            A2ABridgeComplianceConfig(**_config_fields(base_url=base_url))

        allowed = _config_fields(base_url=base_url, allow_private_targets=True)
        assert A2ABridgeComplianceConfig(**allowed).base_url == base_url

    @pytest.mark.parametrize(
        "fields",
        [
            {"base_url": "http://gateway.example"},
            {"base_url": "http://8.8.8.8"},
            {"probe_tokens_url": "http://gateway.example/compliance/probe-tokens"},
        ],
    )
    def test_plain_http_to_a_public_host_is_refused_even_with_the_switch(
        self, fields: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationError, match="loopback or private"):
            A2ABridgeComplianceConfig(**_config_fields(allow_private_targets=True, **fields))

    @pytest.mark.parametrize("switch", [False, True])
    def test_user_info_is_refused_and_never_echoed(self, switch: bool) -> None:
        fields = _config_fields(base_url="https://ops:hunter2@gateway.example")

        with pytest.raises(ValidationError) as excinfo:
            A2ABridgeComplianceConfig(**fields, allow_private_targets=switch)

        assert "hunter2" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "base_url", ["https://gateway.example/a2a", "https://gateway.example/?x=1"]
    )
    def test_the_base_url_is_an_origin(self, base_url: str) -> None:
        with pytest.raises(ValidationError, match="origin"):
            A2ABridgeComplianceConfig(**_config_fields(base_url=base_url))

    @pytest.mark.parametrize(
        "fields",
        [
            {"card_keys": {}},
            {"expected_agent_urn": "urn:asap:agent:Not-Lower"},
            {"card_keys": {"card-1": {"kty": "OKP"}}},
            {"profile": "mcp-auth-http"},
            {"timeout_seconds": 0},
            {"skip_check": ["card_well_known"]},
        ],
    )
    def test_a_bad_value_or_an_unknown_field_is_refused(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            A2ABridgeComplianceConfig(**_config_fields(**fields))

    def test_a_private_key_is_refused_and_never_echoed(self) -> None:
        private_jwk = {**_CARD_KEY.as_dict(private=True), "kid": "card-1"}

        with pytest.raises(ValidationError) as excinfo:
            A2ABridgeComplianceConfig(**_config_fields(card_keys={"card-1": private_jwk}))

        assert private_jwk["d"] not in str(excinfo.value)


def _profile_config(**fields: Any) -> A2ABridgeComplianceConfig:
    base: dict[str, Any] = {
        "base_url": BASE_URL,
        "expected_agent_urn": AGENT_URN,
        "card_keys": {CARD_KID: CARD_PUBLIC_JWK},
        "probe_tokens_url": f"{BASE_URL}{PROBE_TOKENS_PATH}",
    }
    return A2ABridgeComplianceConfig(**{**base, **fields})


async def _run(server: FakeA2AGateway, **config_fields: Any) -> A2ABridgeResult:
    transport = httpx.MockTransport(server.handle)
    return await validate_a2a_bridge_async(
        _profile_config(**config_fields), http_transport=transport
    )


def _failed(result: A2ABridgeResult) -> set[str]:
    return {check.name for check in result.checks if not check.passed}


class TestCardDocumentChecks:
    """The card at the well-known path."""

    async def test_a_gateway_that_follows_s5_passes_every_check(self) -> None:
        result = await _run(FakeA2AGateway())

        assert result.passed, [check for check in result.checks if not check.passed]
        assert result.profile == "a2a-bridge"
        assert [check.name for check in result.checks] == list(A2A_BRIDGE_CHECK_IDS)

    @pytest.mark.parametrize("fault", [Fault.NO_CARD, Fault.CARD_NOT_JSON])
    async def test_a_gateway_with_the_fault_fails_the_check(self, fault: Fault) -> None:
        result = await _run(FakeA2AGateway(fault))

        assert "card_well_known" in _failed(result)
        assert not result.passed

    async def test_the_card_is_fetched_once_per_run(self) -> None:
        server = FakeA2AGateway()

        await _run(server)

        assert server.requests.count("/.well-known/agent-card.json") == 1

    async def test_an_unreachable_gateway_fails_each_check_without_raising(self) -> None:
        attempts: list[str] = []

        def unreachable(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.path)
            raise httpx.ConnectError("connection refused", request=request)

        config = _profile_config()
        result = await validate_a2a_bridge_async(
            config, http_transport=httpx.MockTransport(unreachable)
        )

        assert not any(check.passed for check in result.checks)
        assert all("ConnectError" in check.message for check in result.checks)
        assert attempts.count("/.well-known/agent-card.json") == 1

    async def test_a_skipped_check_passes_with_the_message_skipped(self) -> None:
        result = await _run(FakeA2AGateway(Fault.NO_CARD), skip_checks=["card_well_known"])

        card_check = next(check for check in result.checks if check.name == "card_well_known")
        assert (card_check.passed, card_check.message) == (True, "skipped")

    async def test_a_run_with_no_token_source_is_refused(self) -> None:
        config = _profile_config(probe_tokens_url=None)

        with pytest.raises(ValueError, match="probe_tokens_url"):
            await validate_a2a_bridge_async(config)

    def test_the_sync_wrapper_runs_the_profile(self) -> None:
        transport = httpx.MockTransport(FakeA2AGateway().handle)

        assert validate_a2a_bridge(_profile_config(), http_transport=transport).passed


def _message(result: A2ABridgeResult, check_id: str) -> str:
    return next(check.message for check in result.checks if check.name == check_id)


class TestCardSignatureChecks:
    """The card parses as A2A v1.0, verifies under the configured trust, and names the URN."""

    @pytest.mark.parametrize(
        ("fault", "failed"),
        [
            (Fault.CARD_V03, {"card_a2a_v1", "card_signature"}),
            (Fault.UNSIGNED_CARD, {"card_signature"}),
            (Fault.TAMPERED_CARD, {"card_signature"}),
            (Fault.CARD_FOR_ANOTHER_AGENT, {"card_signature", "card_agent_urn"}),
            (Fault.CARD_WITHOUT_EXTENSION, {"card_agent_urn"}),
        ],
    )
    async def test_a_gateway_with_the_fault_fails_the_checks(
        self, fault: Fault, failed: set[str]
    ) -> None:
        result = await _run(FakeA2AGateway(fault))

        assert failed <= _failed(result)
        assert "card_well_known" not in _failed(result)

    async def test_a_card_without_the_extension_is_bound_to_the_expected_urn(self) -> None:
        result = await _run(FakeA2AGateway(Fault.CARD_WITHOUT_EXTENSION))

        assert _failed(result) == {"card_agent_urn", "card_manifest_same_agent"}

    async def test_a_v03_card_is_named_as_such(self) -> None:
        result = await _run(FakeA2AGateway(Fault.CARD_V03))

        assert "A2A v0.3" in _message(result, "card_a2a_v1")


class TestTheHarnessIsNotFooled:
    """Negative trust checks: the trust is the config's, scoped to the expected URN only."""

    async def test_a_config_that_expects_another_agent_fails_the_signature_check(self) -> None:
        result = await _run(FakeA2AGateway(), expected_agent_urn=OTHER_AGENT_URN)

        assert {"card_signature", "card_agent_urn"} <= _failed(result)
        assert f"may sign for {OTHER_AGENT_URN} only" in _message(result, "card_signature")

    async def test_a_card_for_another_agent_signed_with_the_trusted_key_fails(self) -> None:
        result = await _run(FakeA2AGateway(Fault.CARD_FOR_ANOTHER_AGENT))

        assert f"may sign for {AGENT_URN} only" in _message(result, "card_signature")

    async def test_another_key_under_the_configured_kid_fails_the_signature_check(self) -> None:
        other_key = OKPKey.generate_key("Ed25519", private=True).as_dict(private=False)

        result = await _run(FakeA2AGateway(), card_keys={CARD_KID: {**other_key, "kid": CARD_KID}})

        assert "(bad_signature)" in _message(result, "card_signature")

    async def test_another_key_under_another_kid_is_no_trusted_signature(self) -> None:
        other_key = OKPKey.generate_key("Ed25519", private=True).as_dict(private=False)

        result = await _run(
            FakeA2AGateway(), card_keys={"other-kid": {**other_key, "kid": "other-kid"}}
        )

        assert "no signature on the card is by a configured key" in _message(
            result, "card_signature"
        )
        assert CARD_PUBLIC_JWK["x"] not in str(result.checks)


class TestInterfaceAndManifestChecks:
    """The card names the interface that the calls use, and the same agent as the manifest."""

    @pytest.mark.parametrize(
        ("fault", "check_id"),
        [
            (Fault.CARD_INTERFACE_ELSEWHERE, "card_jsonrpc_interface"),
            (Fault.MANIFEST_OF_ANOTHER_VERSION, "card_manifest_same_agent"),
            (Fault.MANIFEST_ERROR, "card_manifest_same_agent"),
            (Fault.CARD_FOR_ANOTHER_AGENT, "card_manifest_same_agent"),
        ],
    )
    async def test_a_gateway_with_the_fault_fails_the_check(
        self, fault: Fault, check_id: str
    ) -> None:
        result = await _run(FakeA2AGateway(fault))

        assert check_id in _failed(result)

    async def test_an_origin_without_an_asap_manifest_passes(self) -> None:
        result = await _run(FakeA2AGateway(Fault.NO_MANIFEST))

        assert "card_manifest_same_agent" not in _failed(result)

    async def test_the_message_names_what_differs(self) -> None:
        result = await _run(FakeA2AGateway(Fault.MANIFEST_OF_ANOTHER_VERSION))

        assert _message(result, "card_manifest_same_agent").endswith("differ in version")

    async def test_the_interface_must_be_at_the_configured_origin(self) -> None:
        local = {"base_url": "http://127.0.0.1:8788", "allow_private_targets": True}

        result = await _run(FakeA2AGateway(), **local)

        assert "http://127.0.0.1:8788/a2a" in _message(result, "card_jsonrpc_interface")
        assert "card_jsonrpc_interface" in _failed(result)
