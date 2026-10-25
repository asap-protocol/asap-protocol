"""JWKS cache hits are limited to the URI the validator was constructed with.

``fetch_keys(other_uri)`` must not return keys cached for the constructor URI.
A later fetch of the constructor URI after an override is intentionally not
asserted: the cache entry is not tagged with the URI it came from.
"""

from __future__ import annotations

from joserfc import jwk

from asap.auth.jwks import JWKSValidator

_HOME = "https://auth.example.com/jwks.json"
_OTHER = "https://other.example/jwks.json"


def _key_set(kid: str) -> jwk.KeySet:
    """Public Ed25519 key set labeled ``kid``. The private half is not stored."""
    private = jwk.OKPKey.generate_key(crv="Ed25519", private=True)
    public = private.as_dict(private=False)
    public["kid"] = kid
    return jwk.KeySet.import_key_set({"keys": [public]})


async def test_fetch_keys_does_not_reuse_cache_for_another_uri() -> None:
    """An override URI is fetched even when the constructor URI is still cached."""
    calls: list[str] = []

    async def fetcher(uri: str) -> jwk.KeySet:
        calls.append(uri)
        kid = "other" if uri == _OTHER else "home"
        return _key_set(kid)

    validator = JWKSValidator(_HOME, fetcher=fetcher)
    first = await validator.fetch_keys()
    cached = await validator.fetch_keys()
    foreign = await validator.fetch_keys(_OTHER)

    assert calls == [_HOME, _OTHER]
    assert cached is first
    assert first.keys[0].kid == "home"
    assert foreign.keys[0].kid == "other"
    assert foreign is not first
