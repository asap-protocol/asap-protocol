"""Signing keys of allowlisted external issuers (v2.6.0).

An issuer's keys come only from its own config entry: a static JWKS, its
``jwks_uri``, or the ``jwks_uri`` named by its OIDC discovery document. A
token never selects a key set; ``jku``, ``x5u``, and ``jwk`` headers are never
followed. A fetched JWKS keeps only RSA, EC, and OKP keys. Every fetch goes through
:func:`asap.utils.guarded_fetch.guarded_fetch_json`.
``asap.auth.jwks.JWKSValidator`` is not reused: its fetch is unguarded and it
refetches on every failed validation.

Remote keys live for :data:`EXTERNAL_JWKS_CACHE_TTL_SECONDS`. A token with an
unknown ``kid`` (key rotation) causes at most one fetch per
:data:`EXTERNAL_JWKS_MIN_REFETCH_SECONDS` per issuer, and failed fetches count
too, so random ``kid`` values or an unreachable issuer cannot turn requests
into fetches.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from joserfc import jwk
from joserfc.errors import JoseError

from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
    JwksUriKeySource,
    StaticJwksKeySource,
)
from asap.auth.jwks import issuer_matches_expected
from asap.observability import get_logger
from asap.utils.guarded_fetch import AddressResolver, GuardedFetchError, guarded_fetch_json

logger = get_logger(__name__)

EXTERNAL_JWKS_CACHE_TTL_SECONDS = 300.0
EXTERNAL_JWKS_MIN_REFETCH_SECONDS = 30.0
_UNAVAILABLE = ExternalTokenErrorReason.KEY_SOURCE_UNAVAILABLE
_VERIFYING_KEY_TYPES = frozenset({"RSA", "EC", "OKP"})
_OIDC_DISCOVERY_PATH = "/.well-known/openid-configuration"


def _verifying_keys(issuer: str, document: dict[str, Any]) -> jwk.KeySet:
    """Import a fetched JWKS and keep only asymmetric keys; ``oct`` keys are dropped."""
    try:
        imported = jwk.KeySet.import_key_set(cast(jwk.KeySetSerialization, document))
    except (JoseError, KeyError, TypeError, ValueError) as exc:
        raise ExternalTokenError(_UNAVAILABLE, f"issuer {issuer!r}: unusable JWKS: {exc}") from exc
    keys = [key for key in imported.keys if key.key_type in _VERIFYING_KEY_TYPES]
    if not keys:
        raise ExternalTokenError(
            _UNAVAILABLE, f"issuer {issuer!r}: JWKS has no RSA, EC, or OKP key"
        )
    return jwk.KeySet(keys)


class IssuerKeySource:
    """The verification keys of one allowlisted issuer, static or fetched and cached.

    ``resolver`` and ``transport`` are the test seams of the guarded fetch;
    ``clock`` is a monotonic clock in seconds.

    Example:
        >>> keys = await IssuerKeySource(issuer_entry).keys_for(header.get("kid"))
    """

    def __init__(
        self,
        issuer: ExternalIssuerConfig,
        *,
        resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._resolver = resolver
        self._transport = transport
        self._clock = clock
        source = issuer.key_source
        self._static = source.key_set() if isinstance(source, StaticJwksKeySource) else None
        self._keys: jwk.KeySet | None = None
        self._fetched_at = float("-inf")
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    async def keys_for(self, kid: str | None) -> jwk.KeySet:
        """Return the keys to verify a token whose header names ``kid``.

        Raises :class:`ExternalTokenError` with ``key_source_unavailable`` when
        no current keys can be had. An unknown ``kid`` that cannot be refreshed
        yet returns the current keys, and the signature check fails.
        """
        if self._static is not None:
            return self._static
        if self._issuer.key_source is None:
            raise ExternalTokenError(
                _UNAVAILABLE, f"issuer {self._issuer.issuer!r} has no key source"
            )
        async with self._lock:
            now = self._clock()
            if self._keys is not None and self._is_fresh(now) and _holds_kid(self._keys, kid):
                return self._keys
            if self._last_attempt is None or now - self._last_attempt >= (
                EXTERNAL_JWKS_MIN_REFETCH_SECONDS
            ):
                self._last_attempt = now
                await self._refresh(now)
            if self._keys is None or not self._is_fresh(now):
                raise ExternalTokenError(
                    _UNAVAILABLE,
                    f"issuer {self._issuer.issuer!r}: no current keys; the last fetch failed",
                )
            return self._keys

    def _is_fresh(self, now: float) -> bool:
        return now - self._fetched_at < EXTERNAL_JWKS_CACHE_TTL_SECONDS

    async def _refresh(self, now: float) -> None:
        try:
            jwks_uri = await self._jwks_uri()
            self._keys = _verifying_keys(self._issuer.issuer, await self._fetch(jwks_uri))
            self._fetched_at = now
        except ExternalTokenError as exc:
            logger.warning(
                "asap.external_tokens.jwks_refresh_failed",
                issuer=self._issuer.issuer,
                error=str(exc),
            )
            if self._keys is None or not self._is_fresh(now):
                raise

    async def _jwks_uri(self) -> str:
        source = self._issuer.key_source
        if isinstance(source, JwksUriKeySource):
            return source.jwks_uri
        # OpenID Connect Discovery 1.0 §4.3: the document must name this issuer.
        document = await self._fetch(self._issuer.issuer.rstrip("/") + _OIDC_DISCOVERY_PATH)
        named_issuer, jwks_uri = document.get("issuer"), document.get("jwks_uri")
        if not issuer_matches_expected({"iss": named_issuer}, self._issuer.issuer):
            raise ExternalTokenError(
                _UNAVAILABLE, f"discovery for {self._issuer.issuer!r} names {named_issuer!r}"
            )
        if not isinstance(jwks_uri, str):
            raise ExternalTokenError(
                _UNAVAILABLE, f"discovery for {self._issuer.issuer!r} has no jwks_uri string"
            )
        return jwks_uri

    async def _fetch(self, url: str) -> dict[str, Any]:
        try:
            return await guarded_fetch_json(
                url,
                allow_private_hosts=self._issuer.allow_private_hosts,
                resolver=self._resolver,
                transport=self._transport,
            )
        except GuardedFetchError as exc:
            raise ExternalTokenError(_UNAVAILABLE, str(exc)) from exc


def _holds_kid(keys: jwk.KeySet, kid: str | None) -> bool:
    return kid is None or any(key.kid == kid for key in keys.keys)
