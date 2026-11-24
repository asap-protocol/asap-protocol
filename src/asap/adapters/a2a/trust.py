"""Trust store and key resolution for A2A Agent Card signatures.

Keys come from the pinned trust store first. A ``jku`` is used only when it
equals an allowlist entry exactly, character for character (no prefix, host,
or normalized match), and it is fetched only through
:func:`asap.utils.guarded_fetch.guarded_fetch_json`: HTTPS, public addresses
only, a size limit, and a timeout. A ``jku`` off the allowlist causes no request.
A fetched key set is cached per ``jku`` for a bounded lifetime, and a ``kid``
that is not in it does not refetch it: random ``kid`` values cost no request.

Every pinned key and every allowed ``jku`` names the agents it may sign for
(:mod:`asap.adapters.a2a.trust_scope`); there is no implicit "any agent".

Example:
    >>> config = A2ATrustConfig(pinned_keys={"agent-key-1": {
    ...     "jwk": agent_public_jwk, "agents": ["urn:asap:agent:research"]}})  # doctest: +SKIP
    >>> resolver = A2AKeyResolver(config)  # doctest: +SKIP
    >>> await verify_agent_card(card, resolver, algorithms=config.algorithms,
    ...                         policy=config.policy)  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any, Self

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from joserfc import jwk
from joserfc.errors import JoseError
from pydantic import ConfigDict, Field, field_validator, model_validator

from asap.adapters.a2a._ttl_cache import BoundedTTLCache
from asap.adapters.a2a.errors import A2AKeyResolutionError, quoted_prefix, without_user_info
from asap.adapters.a2a.signature_keys import (
    CARD_VERIFY_ALGORITHMS,
    CardKey,
    is_small_order_ed25519,
)
from asap.adapters.a2a.trust_scope import ANY_AGENT, AllowedJku, PinnedCardKey
from asap.adapters.a2a.verification import SignaturePolicy
from asap.models.base import ASAPBaseModel
from asap.utils.guarded_fetch import (
    AddressResolver,
    GuardedFetchError,
    guarded_fetch_json,
    validate_guarded_url,
)

JWKS_MAX_BYTES = 64 * 1024
"""Largest JWKS document a ``jku`` fetch reads."""


class A2ATrustConfig(ASAPBaseModel):
    """Which keys may sign the Agent Cards that ASAP ingests, and how cards are checked.

    ``pinned_keys`` maps a ``kid`` to a :class:`PinnedCardKey`: a public JWK
    (OKP, EC, or RSA) and the agent URNs it may sign for. ``jku_allowlist``
    maps each allowed ``jku`` URL to an :class:`AllowedJku`: the agent URNs
    that the keys of that JWKS may sign for. A ``jku`` is fetched only when it
    is a key of ``jku_allowlist`` exactly. ``allow_private_hosts``
    lets allowlist entries and ``jku`` fetches reach private or loopback hosts,
    for local development and tests only. A fetched JWKS is kept for
    ``jwks_cache_ttl_seconds`` (at most a day), in a cache of at most
    ``jwks_cache_max_entries`` key sets.
    """

    # A validation error must not echo a pinned JWK back: it may hold private members.
    model_config = ConfigDict(hide_input_in_errors=True)

    pinned_keys: dict[str, PinnedCardKey] = Field(default_factory=dict)
    jku_allowlist: dict[str, AllowedJku] = Field(default_factory=dict)
    algorithms: tuple[str, ...] = CARD_VERIFY_ALGORITHMS
    policy: SignaturePolicy = "require_signature"
    allow_private_hosts: bool = False
    jwks_cache_ttl_seconds: float = Field(default=300.0, gt=0, le=86400)
    jwks_cache_max_entries: int = Field(default=32, ge=1, le=1024)

    @field_validator("pinned_keys", mode="before")
    @classmethod
    def _read_unscoped_pinned_keys(cls, keys: Any) -> Any:
        # Transitional: a bare JWK
        # still reads as signing for any agent until every caller is scoped.
        if not isinstance(keys, dict):
            return keys
        return {
            kid: {"jwk": entry, "agents": ANY_AGENT}
            if isinstance(entry, dict) and "kty" in entry
            else entry
            for kid, entry in keys.items()
        }

    @field_validator("jku_allowlist", mode="before")
    @classmethod
    def _read_unscoped_jku_allowlist(cls, urls: Any) -> Any:
        # Transitional, like _read_unscoped_pinned_keys: removed later.
        if isinstance(urls, list | tuple | set | frozenset):
            return {url: {"agents": ANY_AGENT} for url in urls}
        return urls

    @field_validator("pinned_keys")
    @classmethod
    def _check_pinned_keys(cls, keys: dict[str, PinnedCardKey]) -> dict[str, PinnedCardKey]:
        for kid, entry in keys.items():
            import_pinned_key(kid, entry.jwk)
        return keys

    @field_validator("algorithms")
    @classmethod
    def _check_algorithms(cls, algorithms: tuple[str, ...]) -> tuple[str, ...]:
        if not algorithms or not set(algorithms) <= set(CARD_VERIFY_ALGORITHMS):
            raise ValueError(
                f"algorithms must be a non-empty subset of {list(CARD_VERIFY_ALGORITHMS)}"
            )
        return algorithms

    @model_validator(mode="after")
    def _check_jku_allowlist(self) -> Self:
        for url in self.jku_allowlist:
            if without_user_info(url) != url:
                # Checked on the text first, so a URL that does not parse is
                # covered too, and never echoed: the user info may be a credential.
                raise ValueError("a jku_allowlist entry holds user info; a jku never carries one")
            try:
                validate_guarded_url(url, allow_private_hosts=self.allow_private_hosts)
            except GuardedFetchError as exc:
                raise ValueError(f"jku_allowlist entry {url!r} is refused ({exc.code})") from exc
        return self


def import_pinned_key(kid: str, member: Mapping[str, Any]) -> CardKey:
    """Import one pinned JWK; raise ``ValueError`` unless it is a public OKP, EC, or RSA key.

    A small-order Ed25519 key is refused too. The
    message never repeats a key member.
    """
    if member.get("kid", kid) != kid:
        raise ValueError(f"pinned key {kid!r} has a different kid member")
    try:
        key = jwk.import_key(dict(member))
    except (JoseError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"pinned key {kid!r} is not a valid JWK ({type(exc).__name__})") from exc
    if key.key_type == "oct" or key.is_private:
        raise ValueError(f"pinned key {kid!r} must be a public OKP, EC, or RSA key")
    public = key.public_key
    if isinstance(public, Ed25519PublicKey) and is_small_order_ed25519(public):
        raise ValueError(f"pinned key {kid!r} is a small-order Ed25519 key: anyone can sign for it")
    return key


class A2AKeyResolver:
    """The card key resolver for :func:`asap.adapters.a2a.verify_agent_card`.

    ``dns_resolver`` and ``transport`` are passed to the guarded fetch, and
    ``clock`` drives the cache lifetime; all three are for tests.
    """

    def __init__(
        self,
        config: A2ATrustConfig,
        *,
        dns_resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._pinned = {kid: import_pinned_key(kid, e.jwk) for kid, e in config.pinned_keys.items()}
        self._dns_resolver = dns_resolver
        self._transport = transport
        self._jwks_cache: BoundedTTLCache[dict[str, CardKey]] = BoundedTTLCache(
            ttl_seconds=config.jwks_cache_ttl_seconds,
            max_entries=config.jwks_cache_max_entries,
            clock=clock,
        )
        # One lock per allowlisted jku, so concurrent lookups make one request.
        self._fetch_locks: dict[str, asyncio.Lock] = {}

    async def __call__(self, kid: str, jku: str | None) -> CardKey | None:
        """Return the trusted key for *kid*: pinned first, then an allowlisted *jku*.

        Raises:
            A2AKeyResolutionError: The allowlisted *jku* could not be fetched or
                is not a usable JWKS.
        """
        pinned = self._pinned.get(kid)
        if pinned is not None:
            return pinned
        if jku is None or jku not in self.config.jku_allowlist:
            return None
        return (await self._cached_jwks(jku)).get(kid)

    async def _cached_jwks(self, jku: str) -> dict[str, CardKey]:
        async with self._fetch_locks.setdefault(jku, asyncio.Lock()):
            keys = self._jwks_cache.get(jku)
            if keys is None:
                keys = await self._fetch_jwks(jku)
                self._jwks_cache.put(jku, keys)
            return keys

    async def _fetch_jwks(self, jku: str) -> dict[str, CardKey]:
        try:
            document = await guarded_fetch_json(
                jku,
                allow_private_hosts=self.config.allow_private_hosts,
                max_bytes=JWKS_MAX_BYTES,
                resolver=self._dns_resolver,
                transport=self._transport,
            )
        except GuardedFetchError as exc:
            raise A2AKeyResolutionError(jku, exc.code, "the guarded fetch refused it") from exc
        return _keys_by_kid(jku, document)


def _keys_by_kid(jku: str, document: dict[str, Any]) -> dict[str, CardKey]:
    members = document.get("keys")
    if not isinstance(members, list) or not all(isinstance(m, dict) for m in members):
        raise A2AKeyResolutionError(jku, "invalid_jwks", "'keys' must be a list of JWK objects")
    keys: dict[str, CardKey] = {}
    for member in members:
        kid = member.get("kid")
        if not isinstance(kid, str):
            continue
        if kid in keys:
            raise A2AKeyResolutionError(jku, "invalid_jwks", f"kid {quoted_prefix(kid)} repeats")
        try:
            keys[kid] = jwk.import_key(member)
        except (JoseError, ValueError, TypeError, KeyError) as exc:
            detail = f"key {quoted_prefix(kid)} is not a valid JWK ({type(exc).__name__})"
            raise A2AKeyResolutionError(jku, "invalid_jwks", detail) from exc
    return keys
