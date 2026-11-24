"""A local OAuth issuer for tests and examples (v2.6.0).

One signing key, its static JWKS, and access-token minting with chosen claims
and header overrides. An issuer entry from :meth:`LocalIssuer.config_entry`
uses the ``static_jwks`` key source, so nothing is fetched. Other tests and
the gateway example reuse it.

Example:
    >>> issuer = LocalIssuer()
    >>> entry = issuer.config_entry("https://api.example/asap", grant_rules=[rule])
    >>> token = issuer.mint(issuer.claims("https://api.example/asap", scope="files:read"))
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from joserfc import jwk
from joserfc import jwt as jose_jwt

DEFAULT_LOCAL_ISSUER = "https://issuer.example"
LocalIssuerAlgorithm = Literal["ES256", "RS256", "EdDSA"]


def _generate_signing_key(algorithm: LocalIssuerAlgorithm) -> jwk.Key:
    if algorithm == "ES256":
        return jwk.ECKey.generate_key("P-256", private=True, auto_kid=True)
    if algorithm == "RS256":
        return jwk.RSAKey.generate_key(2048, private=True, auto_kid=True)
    return jwk.OKPKey.generate_key("Ed25519", private=True, auto_kid=True)


def _b64url_json(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unsigned_token(header: Mapping[str, Any], claims: Mapping[str, Any]) -> str:
    """Return a compact JWS with an empty signature, for ``alg: none`` tests."""
    return f"{_b64url_json(header)}.{_b64url_json(claims)}."


def nested_payload_token(header: Mapping[str, Any], depth: int = 30_000) -> str:
    """Return a compact JWS whose payload nests ``depth`` arrays in ``iss``.

    Past about 10,000 levels json.loads raises RecursionError; the default
    stays under joserfc's 128,000-byte limit on the encoded payload.
    """
    payload = b'{"iss":' + b"[" * depth + b"]" * depth + b"}"
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_b64url_json(header)}.{encoded}.c2ln"


class LocalIssuer:
    """An access-token issuer that lives in the test process.

    ``issuer`` is the ``iss`` value; ``algorithm`` picks the key type.
    """

    def __init__(
        self, issuer: str = DEFAULT_LOCAL_ISSUER, *, algorithm: LocalIssuerAlgorithm = "ES256"
    ) -> None:
        self.issuer = issuer
        self.algorithm = algorithm
        self.signing_key = _generate_signing_key(algorithm)

    @property
    def kid(self) -> str:
        """The ``kid`` of the current signing key (its RFC 7638 thumbprint)."""
        return str(self.signing_key.kid)

    @property
    def jwks(self) -> dict[str, Any]:
        """The public JWKS for the current key; it never holds the private part."""
        return {"keys": [self.signing_key.as_dict(private=False)]}

    def rotate_key(self) -> None:
        """Replace the signing key; tokens minted before keep the old ``kid``."""
        self.signing_key = _generate_signing_key(self.algorithm)

    def config_entry(
        self,
        audience: str,
        *,
        grant_rules: Iterable[Mapping[str, Any]] = (),
        **overrides: Any,
    ) -> dict[str, Any]:
        """Return an ``ExternalIssuerConfig`` entry with this issuer's static JWKS."""
        entry: dict[str, Any] = {
            "issuer": self.issuer,
            "audience": audience,
            "key_source": {"kind": "static_jwks", "jwks": self.jwks},
            "grant_rules": [dict(rule) for rule in grant_rules],
        }
        entry.update(overrides)
        return entry

    def claims(
        self,
        audience: str | list[str],
        *,
        subject: str = "user-1",
        scope: str | None = None,
        lifetime_seconds: int = 300,
        **extra: Any,
    ) -> dict[str, Any]:
        """Return access-token claims; ``extra`` adds or replaces claims."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "aud": audience,
            "client_id": "test-client",
            "iat": now,
            "exp": now + lifetime_seconds,
        }
        if scope is not None:
            claims["scope"] = scope
        claims.update(extra)
        return claims

    def mint(
        self,
        claims: Mapping[str, Any],
        *,
        header: Mapping[str, Any] | None = None,
        key: jwk.Key | None = None,
    ) -> str:
        """Sign ``claims``. ``header`` replaces fields (``None`` removes one); ``key`` signs instead."""
        merged: dict[str, Any] = {"alg": self.algorithm, "kid": self.kid, "typ": "at+jwt"}
        merged.update(header or {})
        protected = {name: value for name, value in merged.items() if value is not None}
        return jose_jwt.encode(
            protected,
            dict(claims),
            key or self.signing_key,
            algorithms=[protected["alg"]],
            default_type=None,
        )
