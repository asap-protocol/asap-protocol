"""Key resolution for card signatures: pinned keys, then an allowlisted jku.

No test touches the network: DNS and the HTTP transport are fakes, and the
handler records every request, so "no request" is checked, not assumed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from joserfc.jwk import ECKey, OKPKey

from asap.adapters.a2a import (
    A2AKeyResolutionError,
    A2AKeyResolver,
    A2ANoTrustedSignatureError,
    A2ATrustConfig,
    manifest_to_agent_card,
    sign_agent_card,
    verify_agent_card,
)
from asap.adapters.a2a.trust_scope import AllowedJku, PinnedCardKey
from tests.adapters.a2a.helpers import research_card_inputs, research_manifest

_JKU = "https://keys.example.com/.well-known/jwks.json"
_SCOPE = AllowedJku(agents=["urn:asap:agent:research"])
_PUBLIC_ADDRESS = "93.184.216.34"
_SIGNING_KEY = ed25519.Ed25519PrivateKey.generate()
_PINNED_KEY = ec.generate_private_key(ec.SECP256R1())


def _public_jwk(private_key: Any, kid: str) -> dict[str, Any]:
    jose_type = OKPKey if isinstance(private_key, ed25519.Ed25519PrivateKey) else ECKey
    return {**jose_type.import_key(private_key.public_key()).as_dict(private=False), "kid": kid}


class _JwksServer:
    """An httpx transport that serves one JSON document and records every request."""

    def __init__(self, document: Any, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)
        self._document = document
        self._status = status

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._document)


async def _public_dns(hostname: str) -> list[str]:
    return [_PUBLIC_ADDRESS]


def _resolver(server: _JwksServer, **config: Any) -> A2AKeyResolver:
    settings: dict[str, Any] = {"jku_allowlist": {_JKU: _SCOPE}, **config}
    return A2AKeyResolver(
        A2ATrustConfig(**settings), dns_resolver=_public_dns, transport=server.transport
    )


def _jwks(*kids: str) -> dict[str, Any]:
    return {"keys": [_public_jwk(_SIGNING_KEY, kid) for kid in kids]}


async def test_a_pinned_key_wins_over_a_jku_and_causes_no_request() -> None:
    server = _JwksServer(_jwks("pinned"))
    pinned = PinnedCardKey(jwk=_public_jwk(_PINNED_KEY, "pinned"), agents=_SCOPE.agents)
    resolver = _resolver(server, pinned_keys={"pinned": pinned})

    key = await resolver("pinned", _JKU)

    assert key is not None and key.as_dict(private=False)["x"] == _public_jwk(_PINNED_KEY, "p")["x"]
    assert server.requests == []


@pytest.mark.parametrize(
    "jku",
    [
        "https://attacker.example.net/jwks.json",
        f"{_JKU}/",
        _JKU.replace("keys.example.com", "KEYS.example.com"),
        _JKU.replace("https://", "http://"),
        f"{_JKU}?kid=k1",
        "https://keys.example.com/.well-known/",
    ],
)
async def test_a_jku_off_the_allowlist_causes_no_request(jku: str) -> None:
    server = _JwksServer(_jwks("k1"))

    assert await _resolver(server)("k1", jku) is None
    assert server.requests == []


async def test_an_allowlisted_jku_is_fetched_through_the_guarded_fetch() -> None:
    server = _JwksServer(_jwks("k1", "k2"))

    key = await _resolver(server)("k2", _JKU)

    assert key is not None and key.kid == "k2"
    [request] = server.requests
    assert (request.url.host, request.headers["Host"]) == (_PUBLIC_ADDRESS, "keys.example.com")
    assert request.headers["Accept-Encoding"] == "identity"


async def test_a_kid_missing_from_a_fetched_jwks_is_not_trusted() -> None:
    assert await _resolver(_JwksServer(_jwks("k1")))("k9", _JKU) is None


async def test_a_key_without_a_kid_in_a_fetched_jwks_is_ignored() -> None:
    jwks = _jwks("k1")
    jwks["keys"].insert(0, {key: value for key, value in jwks["keys"][0].items() if key != "kid"})

    key = await _resolver(_JwksServer(jwks))("k1", _JKU)

    assert key is not None and key.kid == "k1"


async def test_a_card_whose_jku_is_off_the_allowlist_fails_with_no_request() -> None:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    signed = sign_agent_card(card, _SIGNING_KEY, kid="k1", jku="https://attacker.example.net/jwks")
    server = _JwksServer(_jwks("k1"))
    resolver = _resolver(server)

    with pytest.raises(A2ANoTrustedSignatureError):
        await verify_agent_card(signed, resolver, algorithms=resolver.config.algorithms)

    assert server.requests == []


async def test_a_card_verifies_with_a_key_from_an_allowlisted_jku() -> None:
    card = manifest_to_agent_card(research_manifest(), research_card_inputs()).card
    signed = sign_agent_card(card, _SIGNING_KEY, kid="k1", jku=_JKU)
    server = _JwksServer(_jwks("k1"))

    result = await verify_agent_card(signed, _resolver(server))

    assert result.signer_kids == ("k1",)
    assert len(server.requests) == 1


async def test_a_failed_jku_fetch_fails_with_the_guarded_refusal_code() -> None:
    server = _JwksServer({"error": "unavailable"}, status=503)

    with pytest.raises(A2AKeyResolutionError) as raised:
        await _resolver(server)("k1", _JKU)

    assert (raised.value.refusal, raised.value.jku) == ("http_status", _JKU)
    assert raised.value.code == "asap:adapters/a2a/key_resolution"


async def test_a_jku_host_with_a_private_address_is_refused_before_any_request() -> None:
    async def private_dns(hostname: str) -> list[str]:
        return ["10.0.0.7"]

    server = _JwksServer(_jwks("k1"))
    config = A2ATrustConfig(jku_allowlist={_JKU: _SCOPE})
    resolver = A2AKeyResolver(config, dns_resolver=private_dns, transport=server.transport)

    with pytest.raises(A2AKeyResolutionError) as raised:
        await resolver("k1", _JKU)

    assert raised.value.refusal == "host_not_public"
    assert server.requests == []


@pytest.mark.parametrize(
    "document",
    [
        {"keys": "not a list"},
        {"no_keys": []},
        {"keys": [{"kty": "unknown", "kid": "k1"}]},
        {"keys": ["not an object"]},
        _jwks("k1", "k1"),
    ],
    ids=["keys not a list", "no keys", "bad key", "key not an object", "repeated kid"],
)
async def test_a_document_that_is_not_a_usable_key_set_fails_as_invalid_jwks(
    document: dict[str, Any],
) -> None:
    with pytest.raises(A2AKeyResolutionError) as raised:
        await _resolver(_JwksServer(document))("k1", _JKU)

    assert raised.value.refusal == "invalid_jwks"
