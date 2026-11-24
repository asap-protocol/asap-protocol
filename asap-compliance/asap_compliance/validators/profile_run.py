"""What the HTTP compliance profiles share when they run.

A profile is a list of checks; each check returns ``None`` or a short reason.
:func:`run_profile_checks` turns them into one ``CheckResult`` each, so a
transport error, a provider error, or a missing input fails only its own check
and the run goes on. Two checks judge every answer of a run: each HTTP 401
carries a Bearer challenge, and no answer holds a token that the run sent.

Example::

    checks = [("auth_required", check_auth_required, "HTTP 401 and a Bearer challenge")]
    results = await run_profile_checks(run, checks, skip_checks=config.skip_checks)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Protocol, TypeVar

import httpx

from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.http_answers import HttpAnswer, has_bearer_challenge
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.probe_tokens import (
    ComplianceEndpointTokenProvider,
    ProbeTokenError,
    ProbeTokenProvider,
)

RunT = TypeVar("RunT")
# (check ID, check, what passing means)
ProfileCheck = tuple[str, Callable[[RunT], Awaitable[str | None]], str]
# A JWT part this long belongs to one token; a shorter one could match by chance.
_MIN_ECHO_FRAGMENT = 16


class CheckAbortedError(Exception):
    """A check has nothing to judge (for example, no card); the text quotes no server text."""


class RecordedAnswer(HttpAnswer, Protocol):
    """An answer as a run records it: numbered, with its body text."""

    @property
    def request_id(self) -> int: ...

    @property
    def body_text(self) -> str: ...


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


def unchallenged_401_problem(responses: Sequence[HttpAnswer]) -> str | None:
    """None when every HTTP 401 of the run has a Bearer challenge, and there was one."""
    unauthorized = [response for response in responses if response.status_code == 401]
    if not unauthorized:
        return "the run saw no HTTP 401"
    bare = [response for response in unauthorized if not has_bearer_challenge(response)]
    return f"{len(bare)} of {len(unauthorized)} HTTP 401 answers have none" if bare else None


def echoed_token_problem(
    sent_tokens: Sequence[str], responses: Sequence[RecordedAnswer]
) -> str | None:
    """None when no body or header holds a sent token, or a long dot-separated part of one."""
    fragments = {
        fragment
        for token in sent_tokens
        for fragment in [token, *token.split(".")]
        if len(fragment) >= _MIN_ECHO_FRAGMENT
    }
    echoed = [
        response.request_id
        for response in responses
        for place in [response.body_text, *response.headers.values()]
        if any(fragment in place for fragment in fragments)
    ]
    return f"the answers to requests {sorted(set(echoed))} hold one" if echoed else None
