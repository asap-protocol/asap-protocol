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


def _discovery_document() -> dict[str, object]:
    return {
        "issuer": "https://auth.example.com",
        "token_endpoint": "https://auth.example.com/oauth/token",
        "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
        "scopes_supported": ["openid"],
    }


async def test_oidc_discovery_refetches_at_exact_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery stays cached until the TTL second, then fetches again."""
    clock = _Clock(_OPENED_AT)
    monkeypatch.setattr("asap.auth.oidc.time.time", clock.time)
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_discovery_document())

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
    assert refreshed.issuer == first.issuer
    assert refreshed is not first


async def test_jwks_cache_refetches_at_exact_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWKS stays cached until the 24h TTL second, then fetches again."""
    clock = _Clock(_OPENED_AT)
    monkeypatch.setattr("asap.auth.jwks.time.time", clock.time)
    key = jwk.OKPKey.generate_key("Ed25519")
    jwks_json = {"keys": [key.as_dict(private=False)]}
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=jwks_json)

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
    assert refreshed is not first
