"""Tests for the pytest plugin (fixture and marker)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import asap_compliance
from asap_compliance.config import ComplianceConfig

from .a2a_fake_gateway import AGENT_URN, CARD_KID, CARD_PUBLIC_JWK

if TYPE_CHECKING:
    from _pytest.pytester import Pytester


@pytest.mark.asap_compliance
def test_compliance_harness_fixture(compliance_harness: ComplianceConfig) -> None:
    """Verify compliance_harness fixture returns ComplianceConfig."""
    assert isinstance(compliance_harness, ComplianceConfig)
    assert compliance_harness.agent_url
    assert compliance_harness.timeout_seconds > 0
    assert "handshake" in compliance_harness.test_categories


def test_compliance_config_defaults() -> None:
    """Verify ComplianceConfig default values."""
    from asap_compliance.config import ComplianceConfig

    config = ComplianceConfig(agent_url="https://agent.example.com")
    assert config.agent_url == "https://agent.example.com"
    assert config.timeout_seconds == 30.0
    assert config.test_categories == ["handshake", "schema", "state"]


_PROFILE_PROBE_TEST = """
def test_selected_profile(compliance_profile):
    print("SELECTED", type(compliance_profile).__name__, compliance_profile.profile)
"""
_A2A_BRIDGE_FIELDS = {
    "base_url": "http://127.0.0.1:8788",
    "allow_private_targets": True,
    "expected_agent_urn": AGENT_URN,
    "card_keys": {CARD_KID: CARD_PUBLIC_JWK},
}


def _run_with_plugin(pytester: Pytester, *args: str) -> pytest.RunResult:
    # The package is on the path, not installed, so the inner session loads the plugin by
    # name; pytest-asyncio stays out of it (the probe test is sync).
    return pytester.runpytest("-p", "asap_compliance.pytest_plugin", "-p", "no:asyncio", *args)


class TestSelectAProfile:
    """``--asap-profile`` and the ``compliance_profile`` fixture."""

    @pytest.mark.parametrize(
        ("name", "fields", "config_model"),
        [
            ("a2a-bridge", _A2A_BRIDGE_FIELDS, "A2ABridgeComplianceConfig"),
            (
                "mcp-auth-http",
                {"mcp_url": "http://127.0.0.1:8787/mcp"},
                "McpAuthHttpComplianceConfig",
            ),
            ("mcp-auth-bridge", None, "McpAuthComplianceConfig"),
        ],
    )
    def test_the_option_selects_the_profile_and_its_config_file(
        self, pytester: Pytester, name: str, fields: dict[str, Any] | None, config_model: str
    ) -> None:
        pytester.makepyfile(_PROFILE_PROBE_TEST)
        options = ["--asap-profile", name]
        if fields is not None:
            config_file = pytester.path / "profile.json"
            config_file.write_text(json.dumps(fields), encoding="utf-8")
            options += ["--asap-profile-config", str(config_file)]

        result = _run_with_plugin(pytester, "-s", *options)

        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines([f"*SELECTED {config_model} {name}"])

    def test_an_unknown_profile_is_a_usage_error_that_lists_the_profiles(
        self, pytester: Pytester
    ) -> None:
        pytester.makepyfile(_PROFILE_PROBE_TEST)

        result = _run_with_plugin(pytester, "--asap-profile", "a2a")

        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(["*--asap-profile: invalid choice: 'a2a'*a2a-bridge*"])

    def test_the_fixture_without_a_profile_fails_and_says_which_option_is_missing(
        self, pytester: Pytester
    ) -> None:
        pytester.makepyfile(_PROFILE_PROBE_TEST)

        result = _run_with_plugin(pytester)

        result.assert_outcomes(errors=1)
        result.stdout.fnmatch_lines(["*select a profile with --asap-profile*"])


def test_the_package_version_is_the_one_in_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())

    assert asap_compliance.__version__ == pyproject["project"]["version"] == "1.4.0"
