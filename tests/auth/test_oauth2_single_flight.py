"""Lock the double-checked refresh lock on client-credentials tokens.

``get_valid_token`` must let one caller hit the token endpoint when the cache
is cold. A second caller that passed the unlocked check waits, then reuses
the token the first caller stored.
"""

from __future__ import annotations

import asyncio
import time

from asap.auth.oauth2 import OAuth2ClientCredentials, Token


async def _wait_until_refresh_queued(client: OAuth2ClientCredentials) -> None:
    """Spin the loop until another caller is blocked on the refresh lock."""
    for _ in range(50):
        waiters = getattr(client._refresh_lock, "_waiters", None)
        if waiters:
            return
        await asyncio.sleep(0)
    raise AssertionError("second get_valid_token did not queue on the refresh lock")


async def test_cold_cache_refresh_is_single_flight() -> None:
    """Two overlapping cold-cache reads perform one token fetch."""
    calls = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def fetching(scope: str | None = None) -> Token:
        nonlocal calls
        if scope is not None:
            raise AssertionError(f"unexpected scope {scope!r}")
        calls += 1
        started.set()
        await release.wait()
        return Token(
            access_token="single-flight-token",
            expires_at=int(time.time()) + 3600,
            token_type="Bearer",
        )

    client = OAuth2ClientCredentials(
        client_id="test-client",
        client_secret="test-secret",
        token_url="https://auth.example.com/oauth/token",
    )
    client.get_access_token = fetching

    first = asyncio.create_task(client.get_valid_token())
    await started.wait()
    second = asyncio.create_task(client.get_valid_token())
    await _wait_until_refresh_queued(client)

    assert calls == 1
    release.set()
    first_token, second_token = await asyncio.gather(first, second)

    assert calls == 1
    assert first_token is second_token
    assert first_token.access_token == "single-flight-token"
