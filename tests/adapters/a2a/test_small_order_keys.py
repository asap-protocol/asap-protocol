"""Small-order Ed25519 public keys are never fit to verify a card.

With the identity point as the public key, the signature ``R = identity, S = 0``
verifies any message. Every small-order encoding (the eight points and their
non-canonical encodings) is refused as a key mismatch: for a pinned key when
the trust config is built, and for any key when a signature is checked.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey
from pydantic import ValidationError

from asap.adapters.a2a import (
    A2AInvalidSignatureError,
    A2AKeyResolver,
    A2ATrustConfig,
    manifest_to_agent_card,
    verify_agent_card,
)
from asap.adapters.a2a.signature_keys import check_card_signature
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest
from tests.adapters.a2a.signing_helpers import forged_signature

_P = 2**255 - 19
_IDENTITY = bytes([1]) + bytes(31)
_ORDER_8 = bytes.fromhex("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a")
_SMALL_ORDER = {
    "identity": _IDENTITY,
    "order 2": (_P - 1).to_bytes(32, "little"),
    "order 4": bytes(32),
    "order 8": _ORDER_8,
    "order 8, sign bit set": _ORDER_8[:31] + bytes([_ORDER_8[31] | 0x80]),
    "identity as p + 1": (_P + 1).to_bytes(32, "little"),
    "identity, sign bit set": _IDENTITY[:31] + bytes([0x80]),
}
# R = the identity point, S = 0: valid for every message under the identity key.
_UNIVERSAL_FORGERY = _IDENTITY + bytes(32)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(raw: bytes, kid: str = "k1") -> dict[str, Any]:
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw), "kid": kid}


@pytest.mark.parametrize("raw", list(_SMALL_ORDER.values()), ids=list(_SMALL_ORDER))
def test_a_small_order_key_does_not_fit_and_the_universal_forgery_fails(raw: bytes) -> None:
    key = OKPKey.import_key(_jwk(raw))

    with pytest.raises(A2AInvalidSignatureError) as raised:
        check_card_signature(0, "EdDSA", key, b"any card bytes", _UNIVERSAL_FORGERY)

    assert raised.value.reason == "key_mismatch"


@pytest.mark.parametrize("name", ["identity", "order 8"])
def test_a_small_order_pinned_key_is_refused_when_the_config_is_built(name: str) -> None:
    with pytest.raises(ValidationError, match="small-order"):
        A2ATrustConfig(pinned_keys={"k1": _jwk(_SMALL_ORDER[name])})


async def test_a_small_order_key_from_a_jwks_fails_the_card_as_a_key_mismatch() -> None:
    jku = "https://keys.example.com/jwks.json"
    jwks = {"keys": [_jwk(_IDENTITY)]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=jwks))

    async def public_dns(hostname: str) -> list[str]:
        return ["93.184.216.34"]

    resolver = A2AKeyResolver(
        A2ATrustConfig(jku_allowlist=[jku]), dns_resolver=public_dns, transport=transport
    )
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    header = {"alg": "EdDSA", "kid": "k1", "jku": jku}
    forged = forged_signature(header, signature=_b64url(_UNIVERSAL_FORGERY))

    with pytest.raises(A2AInvalidSignatureError) as raised:
        await verify_agent_card(card.model_copy(update={"signatures": [forged]}), resolver)

    assert raised.value.reason == "key_mismatch"


def test_a_normal_ed25519_key_still_fits() -> None:
    private = ed25519.Ed25519PrivateKey.generate()
    signature = private.sign(b"card")

    check_card_signature(0, "EdDSA", OKPKey.import_key(private.public_key()), b"card", signature)
