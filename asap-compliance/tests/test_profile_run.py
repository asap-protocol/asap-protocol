"""Tests for what the HTTP compliance profiles share when they run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.probe_tokens import ProbeTokenError
from asap_compliance.validators.profile_run import (
    CheckAbortedError,
    echoed_token_problem,
    endpoint_token_provider,
    run_profile_checks,
    unchallenged_401_problem,
)

_TOKEN = "header-part-0123456789.claims-part-0123456789.signature-part-0123456789"


@dataclass(frozen=True)
class _Answer:
    request_id: int
    status_code: int
    body_text: str = ""
    headers: httpx.Headers = field(default_factory=httpx.Headers)
    json_body: dict[str, Any] | None = None

    def describe(self) -> str:
        return f"HTTP {self.status_code}"


async def _passes(run: list[str]) -> str | None:
    run.append("passes")
    return None


async def _finds_a_problem(run: list[str]) -> str | None:
    return "got HTTP 200"


def _raising(error: Exception) -> Any:
    async def check(run: list[str]) -> str | None:
        raise error

    return check


class TestRunProfileChecks:
    async def test_each_check_gives_one_result_in_order(self) -> None:
        run: list[str] = []
        checks = [("a", _passes, "HTTP 401"), ("b", _finds_a_problem, "HTTP 403")]

        results = await run_profile_checks(run, checks, skip_checks=[])

        assert [(r.name, r.passed, r.message) for r in results] == [
            ("a", True, "HTTP 401"),
            ("b", False, "expected HTTP 403; got HTTP 200"),
        ]

    async def test_a_skipped_check_is_not_run_and_passes_as_skipped(self) -> None:
        run: list[str] = []

        [result] = await run_profile_checks(run, [("a", _passes, "x")], skip_checks=["a"])

        assert (result.passed, result.message, run) == (True, "skipped", [])

    @pytest.mark.parametrize(
        "error",
        [
            HttpExchangeError("GET failed: ConnectError"),
            ProbeTokenError("no token"),
            CheckAbortedError("no card"),
        ],
    )
    async def test_a_transport_provider_or_input_error_fails_only_its_check(
        self, error: Exception
    ) -> None:
        checks = [("a", _raising(error), "x"), ("b", _passes, "y")]

        first, second = await run_profile_checks([], checks, skip_checks=[])

        assert (first.passed, first.message) == (False, f"expected x; {error}")
        assert second.passed

    async def test_any_other_error_is_a_bug_and_propagates(self) -> None:
        with pytest.raises(KeyError):
            await run_profile_checks([], [("a", _raising(KeyError("x")), "x")], skip_checks=[])


class TestTokenSource:
    def test_no_probe_url_and_no_provider_is_refused_with_the_profile_name(self) -> None:
        with pytest.raises(ValueError, match="the a2a-bridge profile needs fresh probe tokens"):
            endpoint_token_provider("a2a-bridge", None, timeout_seconds=1.0, http_transport=None)


class TestRunWideChecks:
    def test_every_401_needs_a_bearer_challenge_and_a_run_needs_one(self) -> None:
        bare = _Answer(1, 401)
        challenged = _Answer(2, 401, headers=httpx.Headers({"www-authenticate": "Bearer"}))

        assert unchallenged_401_problem([_Answer(3, 200)]) == "the run saw no HTTP 401"
        assert unchallenged_401_problem([bare, challenged]) == "1 of 2 HTTP 401 answers have none"
        assert unchallenged_401_problem([challenged]) is None

    @pytest.mark.parametrize(
        "answer",
        [
            _Answer(4, 401, body_text=f"refused {_TOKEN}"),
            _Answer(4, 200, body_text="claims-part-0123456789"),
            _Answer(4, 401, headers=httpx.Headers({"x-seen": "signature-part-0123456789"})),
        ],
    )
    def test_a_token_or_a_long_part_of_one_in_any_answer_is_found(self, answer: _Answer) -> None:
        problem = echoed_token_problem([_TOKEN], [_Answer(1, 200), answer])

        assert problem == "the answers to requests [4] hold one"
        assert _TOKEN not in problem

    def test_a_short_part_is_not_an_echo(self) -> None:
        assert echoed_token_problem(["ab.cd.ef"], [_Answer(1, 200, body_text="ab cd ef")]) is None
