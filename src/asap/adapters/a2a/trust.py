"""Trust store and key resolution for A2A Agent Card signatures.

Keys come from the pinned trust store first. A ``jku`` is used only when it
equals an allowlist entry exactly, character for character (no prefix, host,
or normalized match), and it is fetched only through
:func:`asap.utils.guarded_fetch.guarded_fetch_json`: HTTPS, public addresses
only, a size limit, and a timeout. A ``jku`` off the allowlist causes no request.

Example:
    >>> config = A2ATrustConfig(pinned_keys={"agent-key-1": agent_public_jwk})  # doctest: +SKIP
    >>> resolver = A2AKeyResolver(config)  # doctest: +SKIP
    >>> await verify_agent_card(card, resolver, algorithms=config.algorithms,
    ...                         policy=config.policy)  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

import httpx
from joserfc import jwk
from joserfc.errors import JoseError
from pydantic import ConfigDict, Field, field_validator, model_validator

from asap.adapters.a2a.errors import A2AKeyResolutionError
from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, CardKey
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

    ``pinned_keys`` maps a ``kid`` to a public JWK (OKP, EC, or RSA). A ``jku``
    is fetched only when it is in ``jku_allowlist`` exactly. ``allow_private_hosts``
    lets allowlist entries and ``jku`` fetches reach private or loopback hosts,
    for local development and tests only.
    """

    # A validation error must not echo a pinned JWK back: it may hold private members.
    model_config = ConfigDict(hide_input_in_errors=True)

    pinned_keys: dict[str, dict[str, Any]] = Field(default_factory=dict)
    jku_allowlist: frozenset[str] = frozenset()
    algorithms: tuple[str, ...] = CARD_VERIFY_ALGORITHMS
    policy: SignaturePolicy = "require_signature"
    allow_private_hosts: bool = False

    @field_validator("pinned_keys")
    @classmethod
    def _check_pinned_keys(cls, keys: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for kid, member in keys.items():
            import_pinned_key(kid, member)
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
            if httpx.URL(url).userinfo:
                # Checked first and not echoed: the user info may be a credential.
                raise ValueError("a jku_allowlist entry holds user info; a jku never carries one")
            try:
                validate_guarded_url(url, allow_private_hosts=self.allow_private_hosts)
            except GuardedFetchError as exc:
                raise ValueError(f"jku_allowlist entry {url!r} is refused ({exc.code})") from exc
        return self


def import_pinned_key(kid: str, member: Mapping[str, Any]) -> CardKey:
    """Import one pinned JWK; raise ``ValueError`` unless it is a public OKP, EC, or RSA key.

    The message never repeats a key member.
    """
    if member.get("kid", kid) != kid:
        raise ValueError(f"pinned key {kid!r} has a different kid member")
    try:
        key = jwk.import_key(dict(member))
    except (JoseError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"pinned key {kid!r} is not a valid JWK ({type(exc).__name__})") from exc
    if key.key_type == "oct" or key.is_private:
        raise ValueError(f"pinned key {kid!r} must be a public OKP, EC, or RSA key")
    return key


class A2AKeyResolver:
    """The card key resolver for :func:`asap.adapters.a2a.verify_agent_card`.

    ``dns_resolver`` and ``transport`` are passed to the guarded fetch, for tests.
    """

    def __init__(
        self,
        config: A2ATrustConfig,
        *,
        dns_resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._pinned = {kid: import_pinned_key(kid, m) for kid, m in config.pinned_keys.items()}
        self._dns_resolver = dns_resolver
        self._transport = transport

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
        return (await self._fetch_jwks(jku)).get(kid)

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
            raise A2AKeyResolutionError(jku, "invalid_jwks", f"kid {kid[:64]!r} repeats")
        try:
            keys[kid] = jwk.import_key(member)
        except (JoseError, ValueError, TypeError, KeyError) as exc:
            detail = f"key {kid[:64]!r} is not a valid JWK ({type(exc).__name__})"
            raise A2AKeyResolutionError(jku, "invalid_jwks", detail) from exc
    return keys
