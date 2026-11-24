"""Trust configuration for A2A Agent Card signatures.

Keys come from the pinned trust store first. A ``jku`` is used only when it
equals an allowlist entry exactly, character for character (no prefix, host,
or normalized match). Every allowlist entry passes the guarded-fetch URL check
(HTTPS; no non-public IP literal or ``localhost``) when the config is built.

Example:
    >>> config = A2ATrustConfig(pinned_keys={"agent-key-1": agent_public_jwk})  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from joserfc import jwk
from joserfc.errors import JoseError
from pydantic import ConfigDict, Field, field_validator, model_validator

from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, CardKey
from asap.adapters.a2a.verification import SignaturePolicy
from asap.models.base import ASAPBaseModel
from asap.utils.guarded_fetch import GuardedFetchError, validate_guarded_url


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
