"""Lock OIDC discovery and JWKS cache expiry at the configured TTL.

Both caches store ``expires_at = time.time() + ttl`` and treat
``time.time() >= expires_at`` as stale. One second early must not refetch;
the equal second must.
"""

from __future__ import annotations

import httpx
import pytest
from joserfc import jwk

from asap.auth.jwks import JWKS_CACHE_TTL_SECONDS, JWKSValidator
from asap.auth.oidc import DISCOVERY_CACHE_TTL_SECONDS, OIDCDiscovery

_OPENED_AT = 1_700_000_000.0


class _Clock:
    """Mutable stand-in for ``time.time`` so a test can step the cache clock."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def _discovery_document(token_endpoint: str) -> dict[str, object]:
    return {
        "issuer": "https://auth.example.com",
        "token_endpoint": token_endpoint,
        "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
        "scopes_supported": ["openid"],
    }


def _jwks_document(kid: str) -> dict[str, object]:
    key = jwk.OKPKey.generate_key("Ed25519")
    public = key.as_dict(private=False)
    public["kid"] = kid
    return {"keys": [public]}


def _kid(key_set: jwk.KeySet) -> str | None:
    kid = key_set.keys[0].kid
    return kid if isinstance(kid, str) else None


async def test_oidc_discovery_refetches_at_exact_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery stays cached until the TTL second, then fetches again."""
    clock = _Clock(_OPENED_AT)
    monkeypatch.setattr("asap.auth.oidc.time.time", clock.time)
    calls = 0
    endpoints = (
        "https://auth.example.com/oauth/token",
        "https://auth.example.com/oauth/token-rotated",
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = _discovery_document(endpoints[calls])
        calls += 1
        return httpx.Response(200, json=body)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )
    ttl = DISCOVERY_CACHE_TTL_SECONDS

    first = await discovery.discover()
    clock.now = _OPENED_AT + ttl - 1
    cached = await discovery.discover()
    clock.now = _OPENED_AT + ttl
    refreshed = await discovery.discover()

    assert calls == 2
    assert cached is first
    assert first.token_endpoint == endpoints[0]
    assert cached.token_endpoint == endpoints[0]
    assert refreshed.token_endpoint == endpoints[1]
    assert refreshed is not first


async def test_jwks_cache_refetches_at_exact_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWKS stays cached until the 24h TTL second, then fetches again."""
    clock = _Clock(_OPENED_AT)
    monkeypatch.setattr("asap.auth.jwks.time.time", clock.time)
    kids = ("kid-cached", "kid-refreshed")
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = _jwks_document(kids[calls])
        calls += 1
        return httpx.Response(200, json=body)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )
    ttl = JWKS_CACHE_TTL_SECONDS

    first = await validator.fetch_keys()
    clock.now = _OPENED_AT + ttl - 1
    cached = await validator.fetch_keys()
    clock.now = _OPENED_AT + ttl
    refreshed = await validator.fetch_keys()

    assert calls == 2
    assert cached is first
    assert _kid(first) == kids[0]
    assert _kid(cached) == kids[0]
    assert _kid(refreshed) == kids[1]
    assert refreshed is not first
