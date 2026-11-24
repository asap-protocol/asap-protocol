"""A2A bridge compliance validation (profile ``a2a-bridge``).

Runs against a remote A2A v1.0 agent served by an ASAP gateway, over plain HTTP
(:mod:`asap_compliance.validators.a2a_transport`). The card checks fetch the
card once and judge it under the trust of the profile config only: the
expected agent URN and the card keys, never a key or a URN from the target. A
verified card proves who signed it, not where it was served from or that it is
fresh, and no check claims either. ``_PROFILE_CHECKS`` lists the check IDs;
each check picks the credential of its case.

Example::

    config = A2ABridgeComplianceConfig(
        base_url="http://127.0.0.1:8788",
        allow_private_targets=True,  # local and CI runs only
        expected_agent_urn="urn:asap:agent:a2a-gateway-demo",
        card_keys={"gateway-card-1": card_public_jwk},
        probe_tokens_url="http://127.0.0.1:8788/compliance/probe-tokens",
    )
    result = await validate_a2a_bridge_async(config)
    assert result.passed, [check for check in result.checks if not check.passed]
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from asap_compliance.config import A2ABridgeComplianceConfig
from asap_compliance.validators.a2a_transport import (
    AGENT_CARD_PATH,
    A2AHttpResponse,
    A2AHttpTransport,
)
from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.probe_tokens import ProbeTokenProvider
from asap_compliance.validators.profile_run import (
    ProfileCheck,
    endpoint_token_provider,
    run_profile_checks,
)

_EXPECT_CARD_DOCUMENT = f"HTTP 200 and a JSON object at {AGENT_CARD_PATH}"


@dataclass
class A2ABridgeResult:
    """Summary for the ``a2a-bridge`` compliance profile: one entry per check ID."""

    profile: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass
class _BridgeRun:
    """One profile run: the target, the token source, and every token and answer so far."""

    config: A2ABridgeComplianceConfig
    transport: A2AHttpTransport
    tokens: ProbeTokenProvider
    sent_tokens: list[str] = field(default_factory=list)
    responses: list[A2AHttpResponse] = field(default_factory=list)
    _card_answer: A2AHttpResponse | HttpExchangeError | None = None

    async def card_answer(self) -> A2AHttpResponse:
        """The answer at the card's well-known path, fetched once per run, failure included."""
        if self._card_answer is None:
            try:
                self._card_answer = await self.transport.fetch_card()
                self.responses.append(self._card_answer)
            except HttpExchangeError as exc:
                self._card_answer = exc
        if isinstance(self._card_answer, HttpExchangeError):
            raise self._card_answer
        return self._card_answer


def _card_document_problem(response: A2AHttpResponse) -> str | None:
    if response.status_code != 200:
        return f"GET {AGENT_CARD_PATH} answered HTTP {response.status_code}"
    if response.json_body is None:
        return f"GET {AGENT_CARD_PATH} answered with a body that is not a JSON object"
    return None


async def _check_card_well_known(run: _BridgeRun) -> str | None:
    """The card at the well-known path, with no token."""
    return _card_document_problem(await run.card_answer())


# The checks, in the order they run.
_PROFILE_CHECKS: tuple[ProfileCheck[_BridgeRun], ...] = (
    ("card_well_known", _check_card_well_known, _EXPECT_CARD_DOCUMENT),
)

A2A_BRIDGE_CHECK_IDS: tuple[str, ...] = tuple(check_id for check_id, _, _ in _PROFILE_CHECKS)


async def validate_a2a_bridge_async(
    config: A2ABridgeComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> A2ABridgeResult:
    """Run the ``a2a-bridge`` profile against a remote A2A gateway.

    Args:
        config: Profile configuration: the origin, the card trust, and the skill to call.
        token_provider: Source of fresh probe tokens, asked once per request that needs
            one. Default: the gateway's test-only probe endpoint at ``probe_tokens_url``.
        http_transport: Optional httpx transport, for tests with a mock server.

    Returns:
        One ``CheckResult`` per ID in ``A2A_BRIDGE_CHECK_IDS``, in that order. A check
        that gets no full HTTP answer, or no token, fails, and the run goes on.

    Raises:
        ValueError: if there is neither a ``token_provider`` nor a ``probe_tokens_url``.
    """
    if token_provider is None:
        token_provider = endpoint_token_provider(
            config.profile,
            config.probe_tokens_url,
            timeout_seconds=config.timeout_seconds,
            http_transport=http_transport,
        )
    transport = A2AHttpTransport(
        config.base_url, timeout_seconds=config.timeout_seconds, http_transport=http_transport
    )
    run = _BridgeRun(config, transport, token_provider)
    checks = await run_profile_checks(run, _PROFILE_CHECKS, skip_checks=config.skip_checks)
    return A2ABridgeResult(profile=config.profile, checks=checks)


def validate_a2a_bridge(
    config: A2ABridgeComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> A2ABridgeResult:
    """Sync wrapper for :func:`validate_a2a_bridge_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        profile_run = validate_a2a_bridge_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
        return asyncio.run(profile_run)
    raise RuntimeError(
        "Cannot call sync validate_a2a_bridge from inside a running event loop. "
        "Use validate_a2a_bridge_async."
    )
