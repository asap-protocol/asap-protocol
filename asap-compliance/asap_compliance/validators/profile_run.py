"""What the HTTP compliance profiles share when they run.

A profile is a list of checks; each check returns ``None`` or a short reason.
:func:`run_profile_checks` turns them into one ``CheckResult`` each, so a
transport error, a provider error, or a missing input fails only its own check
and the run goes on.

Example::

    checks = [("auth_required", check_auth_required, "HTTP 401 and a Bearer challenge")]
    results = await run_profile_checks(run, checks, skip_checks=config.skip_checks)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import TypeVar

import httpx

from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.probe_tokens import (
    ComplianceEndpointTokenProvider,
    ProbeTokenError,
    ProbeTokenProvider,
)

RunT = TypeVar("RunT")
# (check ID, check, what passing means)
ProfileCheck = tuple[str, Callable[[RunT], Awaitable[str | None]], str]


class CheckAbortedError(Exception):
    """A check has nothing to judge (for example, no card); the text quotes no server text."""


async def run_profile_checks(
    run: RunT, checks: Sequence[ProfileCheck[RunT]], *, skip_checks: Collection[str]
) -> list[CheckResult]:
    """Run each check on *run*, in order; a skipped check passes with the message ``skipped``."""
    return [await _run_check(run, *check, skip_checks) for check in checks]


async def _run_check(
    run: RunT,
    check_id: str,
    check: Callable[[RunT], Awaitable[str | None]],
    passing: str,
    skip_checks: Collection[str],
) -> CheckResult:
    if check_id in skip_checks:
        return CheckResult(check_id, True, "skipped")
    try:
        problem = await check(run)
    except (HttpExchangeError, ProbeTokenError, CheckAbortedError) as exc:
        problem = str(exc)
    if problem is None:
        return CheckResult(check_id, True, passing)
    return CheckResult(check_id, False, f"expected {passing}; {problem}")


def endpoint_token_provider(
    profile: str,
    probe_tokens_url: str | None,
    *,
    timeout_seconds: float,
    http_transport: httpx.AsyncBaseTransport | None,
) -> ProbeTokenProvider:
    """The probe endpoint provider; raise ``ValueError`` when there is no URL."""
    if probe_tokens_url is None:
        raise ValueError(
            f"the {profile} profile needs fresh probe tokens: got probe_tokens_url=None "
            "and no token_provider; set one of them"
        )
    return ComplianceEndpointTokenProvider(
        probe_tokens_url, timeout_seconds=timeout_seconds, http_transport=http_transport
    )
