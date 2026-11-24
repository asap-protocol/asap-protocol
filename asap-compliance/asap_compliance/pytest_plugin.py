"""Pytest plugin for ASAP compliance harness.

Provides:
- Fixture: compliance_harness
- Fixture: compliance_profile, the config of the profile that --asap-profile selects
- Marker: @pytest.mark.asap_compliance
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from asap_compliance.config import ComplianceConfig
from asap_compliance.harness import COMPLIANCE_PROFILES, ProfileConfig, profile_config

MARKER_NAME = "asap_compliance"
MARKER_HELP = "Marks a test as an ASAP protocol compliance test."


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("asap-compliance", description="ASAP protocol compliance options")
    group.addoption(
        "--asap-agent-url",
        action="store",
        dest="asap_agent_url",
        default=os.environ.get("ASAP_AGENT_URL", "http://localhost:8000"),
        help="Base URL of the ASAP agent under test (default: ASAP_AGENT_URL or http://localhost:8000)",
    )
    group.addoption(
        "--asap-timeout",
        action="store",
        type=float,
        dest="asap_timeout",
        default=30.0,
        help="HTTP timeout in seconds for compliance requests (default: 30.0)",
    )
    group.addoption(
        "--asap-profile",
        action="store",
        dest="asap_profile",
        default=None,
        choices=COMPLIANCE_PROFILES,
        help="Compliance profile whose config the compliance_profile fixture builds",
    )
    group.addoption(
        "--asap-profile-config",
        action="store",
        dest="asap_profile_config",
        default=None,
        help="JSON file with the config fields of the profile that --asap-profile selects",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", f"{MARKER_NAME}: {MARKER_HELP}")


@pytest.fixture
def compliance_harness(request: pytest.FixtureRequest) -> ComplianceConfig:
    config = request.config
    agent_url = config.getoption("asap_agent_url", default="http://localhost:8000")
    timeout = config.getoption("asap_timeout", default=30.0)
    return ComplianceConfig(
        agent_url=agent_url,
        timeout_seconds=timeout,
        test_categories=["handshake", "schema", "state"],
    )


@pytest.fixture
def compliance_profile(request: pytest.FixtureRequest) -> ProfileConfig:
    """The config of the profile that ``--asap-profile`` selects, from ``--asap-profile-config``.

    Example::

        def test_profile(compliance_profile):
            result = validate_profile(compliance_profile)
            assert result.passed, [check for check in result.checks if not check.passed]
    """
    name = request.config.getoption("asap_profile")
    if name is None:
        pytest.fail(
            f"select a profile with --asap-profile ({', '.join(COMPLIANCE_PROFILES)})",
            pytrace=False,
        )
    path = request.config.getoption("asap_profile_config")
    fields = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    if not isinstance(fields, dict):
        kind = type(fields).__name__
        pytest.fail(f"--asap-profile-config must hold a JSON object, not {kind}", pytrace=False)
    return profile_config(name, fields)
