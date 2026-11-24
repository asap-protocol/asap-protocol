"""Credentials in a URL never reach an A2A adapter error.

A card URL or a ``jku`` with user info (``user:password@``) is refused, and the
refusal must not repeat the password: not in the message, not in ``details``,
and not in the formatted traceback, which also prints the chained cause.
"""

from __future__ import annotations

import traceback

import httpx
import pytest
from pydantic import ValidationError

from asap.adapters.a2a import (
    A2ACardFetchError,
    A2AKeyResolutionError,
    A2ATrustConfig,
    AgentCardIngestor,
)

_SECRET = "s3cret-pa55word"


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _everything_shown(error: BaseException) -> str:
    details = getattr(error, "details", {})
    return f"{error} {details} {''.join(traceback.format_exception(error))}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://agent:{_SECRET}@agent.example.com/.well-known/agent-card.json",
        f"https://agent:{_SECRET}@[::1",
    ],
    ids=["parseable", "unparseable"],
)
async def test_a_card_url_with_user_info_is_refused_without_repeating_the_password(
    url: str,
) -> None:
    server = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ingestor = AgentCardIngestor(A2ATrustConfig(), dns_resolver=_public_dns, transport=server)

    with pytest.raises(A2ACardFetchError) as raised:
        await ingestor.ingest(url)

    assert raised.value.refusal == "invalid_url"
    assert _SECRET not in _everything_shown(raised.value)
    assert _SECRET not in raised.value.url


def test_a_key_resolution_error_drops_the_user_info_of_its_jku() -> None:
    error = A2AKeyResolutionError(
        f"https://keys:{_SECRET}@keys.example.com/jwks.json", "http_status", "status 503"
    )

    assert _SECRET not in _everything_shown(error)
    assert error.jku == "https://keys.example.com/jwks.json"


@pytest.mark.parametrize(
    "entry",
    ["https://[::1", f"https://keys:{_SECRET}@[::1", "https://keys.example.com:port/jwks"],
    ids=["no closing bracket", "user info and no closing bracket", "port not a number"],
)
def test_a_malformed_allowlist_entry_is_a_validation_error_without_the_password(
    entry: str,
) -> None:
    with pytest.raises(ValidationError, match="jku_allowlist") as raised:
        A2ATrustConfig(jku_allowlist=[entry])

    assert _SECRET not in str(raised.value)
