"""Unit tests for JWKS validation and JWT signature verification."""

import base64
import json
import time

import httpx
import pytest
from joserfc import jwk
from joserfc import jwt as jose_jwt
from joserfc.errors import (
    BadSignatureError,
    DecodeError,
    ExpiredTokenError,
    JoseError,
    MissingClaimError,
)

from asap.auth.jwks import JWKSValidator, fetch_keys, validate_jwt


def _make_jwks_response(public_key: jwk.RSAKey) -> dict:
    """Build JWKS JSON response from RSA key."""
    return {"keys": [public_key.as_dict(private=False)]}


async def test_fetch_keys_returns_key_set_from_mocked_jwks() -> None:
    """Verify fetch_keys returns KeySet from mocked JWKS endpoint."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "jwks" in str(request.url) or "jwks.json" in str(request.url)
        return httpx.Response(200, json=jwks_json)

    key_set = await fetch_keys(
        "https://auth.example.com/.well-known/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    assert key_set is not None
    assert len(key_set.keys) >= 1


async def test_validate_jwt_succeeds_with_mocked_jwks() -> None:
    """Verify validate_jwt validates token signed by mocked JWKS."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:test", "scope": "asap:execute", "exp": now + 3600},
        key,
    )

    claims = validate_jwt(token, key_set)

    assert claims["sub"] == "urn:asap:agent:test"
    assert "asap:execute" in (claims.get("scope") or "").split()


async def test_validate_jwt_raises_on_expired_token() -> None:
    """Verify validate_jwt raises JoseError when token exp is in the past."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    now = int(time.time())
    expired_token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:test", "scope": "asap:execute", "exp": now - 3600},
        key,
    )

    with pytest.raises((ExpiredTokenError, JoseError)):
        validate_jwt(expired_token, key_set)


async def test_validate_jwt_raises_on_missing_exp_claim() -> None:
    """Verify validate_jwt raises when token has no exp claim."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    token_without_exp = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:test", "scope": "asap:execute"},
        key,
    )

    with pytest.raises((MissingClaimError, JoseError)):
        validate_jwt(token_without_exp, key_set)


async def test_validate_jwt_raises_on_invalid_signature() -> None:
    """Verify validate_jwt raises JoseError when signature is invalid."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    # Sign with a different key
    other_key = jwk.RSAKey.generate_key(2048, private=True)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "test", "exp": now + 3600},
        other_key,
    )

    with pytest.raises((BadSignatureError, JoseError)):
        validate_jwt(token, key_set)


async def test_validate_jwt_raises_on_malformed_token() -> None:
    """Verify validate_jwt raises JoseError on malformed token."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    with pytest.raises((DecodeError, JoseError)):
        validate_jwt("not.a.valid.jwt", key_set)


async def test_validate_jwt_accepts_matching_issuer_and_audience() -> None:
    """Verify validate_jwt accepts token when iss/aud match configured expectations."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:test",
            "scope": "asap:execute",
            "exp": now + 3600,
            "iss": "https://auth.example.com/",
            "aud": ["urn:asap:agent:server", "other"],
        },
        key,
    )

    claims = validate_jwt(
        token,
        key_set,
        expected_issuer="https://auth.example.com",
        expected_audience="urn:asap:agent:server",
    )

    assert claims["sub"] == "urn:asap:agent:test"


async def test_validate_jwt_raises_on_invalid_issuer() -> None:
    """Verify validate_jwt raises when iss does not match expected_issuer."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:test",
            "exp": now + 3600,
            "iss": "https://evil.example.com",
        },
        key,
    )

    with pytest.raises(JoseError):
        validate_jwt(
            token,
            key_set,
            expected_issuer="https://auth.example.com",
        )


async def test_validate_jwt_raises_on_invalid_audience() -> None:
    """Verify validate_jwt raises when aud does not match expected_audience."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:test",
            "exp": now + 3600,
            "iss": "https://auth.example.com",
            "aud": "wrong-audience",
        },
        key,
    )

    with pytest.raises(JoseError):
        validate_jwt(
            token,
            key_set,
            expected_issuer="https://auth.example.com",
            expected_audience="urn:asap:agent:server",
        )


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding (JWT format)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def test_validate_jwt_raises_on_none_algorithm_token() -> None:
    """Verify validate_jwt raises JoseError for token with alg 'none' (SEC-01)."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    key_set = jwk.KeySet.import_key_set(_make_jwks_response(key))

    header = json.dumps({"alg": "none", "typ": "JWT"}).encode()
    payload = json.dumps({"sub": "urn:asap:agent:test", "exp": int(time.time()) + 3600}).encode()
    token = f"{_base64url_encode(header)}.{_base64url_encode(payload)}."

    with pytest.raises(JoseError):
        validate_jwt(token, key_set)


def test_validate_jwt_rejects_hs256_when_hmac_key_is_present() -> None:
    """HMAC is outside the allowlist even when the signing key is in the set.

    RFC 8725 section 3.2. Adding HS256 to the decode allowlist would accept
    this token. The ``alg: none`` case does not use a real HMAC key.
    """
    hmac_key = jwk.OctKey.import_key(b"a" * 32)
    key_set = jwk.KeySet([hmac_key])
    token = jose_jwt.encode(
        {"alg": "HS256", "typ": "JWT"},
        {"sub": "urn:asap:agent:test", "exp": int(time.time()) + 3600},
        hmac_key,
    )

    with pytest.raises(JoseError):
        validate_jwt(token, key_set)


async def test_jwks_validator_fetch_keys() -> None:
    """Verify JWKSValidator.fetch_keys returns KeySet from endpoint."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    key_set = await validator.fetch_keys()

    assert key_set is not None
    assert len(key_set.keys) >= 1


async def test_jwks_validator_validate_token_succeeds() -> None:
    """Verify validate_token validates token signed by mocked JWKS."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "urn:asap:agent:client", "scope": "asap:execute", "exp": now + 3600},
        key,
    )

    claims = await validator.validate_token(token)

    assert claims["sub"] == "urn:asap:agent:client"
    assert validator._key_set is not None


async def test_jwks_validator_validate_token_refetches_on_invalid_token() -> None:
    """Verify unknown kid / invalid token triggers JWKS refresh (key rotation)."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    # Cache wrong key (simulates key rotation - token signed with new key)
    wrong_key = jwk.RSAKey.generate_key(2048, private=True)
    validator._key_set = jwk.KeySet.import_key_set(_make_jwks_response(wrong_key))

    # Token signed with correct key; mock returns correct key on refetch
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "test", "exp": now + 3600},
        key,
    )

    claims = await validator.validate_token(token)

    assert claims["sub"] == "test"
    assert call_count >= 1


async def test_jwks_validator_validate_token_raises_after_refetch_fails() -> None:
    """Verify validate_token raises when validation fails even after refetch."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    other_key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(other_key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    # Token signed with key not in JWKS
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "test", "exp": now + 3600},
        key,
    )

    with pytest.raises(JoseError):
        await validator.validate_token(token)


async def test_jwks_validator_fetch_keys_raises_on_http_error() -> None:
    """Verify fetch_keys raises on HTTP error."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await validator.fetch_keys()


async def test_jwks_validator_second_fetch_keys_uses_cache() -> None:
    """Verify second fetch_keys() uses cache (no extra HTTP call)."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
    )

    key_set_1 = await validator.fetch_keys()
    key_set_2 = await validator.fetch_keys()

    assert key_set_1 is key_set_2
    assert call_count == 1


async def test_jwks_validator_validate_token_rejects_wrong_issuer() -> None:
    """Verify JWKSValidator enforces expected_issuer like module validate_jwt."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
        expected_issuer="https://auth.example.com",
        expected_audience="urn:asap:agent:server",
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {
            "sub": "urn:asap:agent:test",
            "exp": now + 3600,
            "iss": "https://evil.example.com",
            "aud": "urn:asap:agent:server",
        },
        key,
    )

    with pytest.raises(JoseError):
        await validator.validate_token(token)


async def test_jwks_validator_validate_token_accepts_matching_iss_aud() -> None:
    """Verify JWKSValidator accepts token when iss/aud match configured expectations."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_json)

    validator = JWKSValidator(
        "https://auth.example.com/jwks.json",
        transport=httpx.MockTransport(mock_handler),
        expected_issuer="https://auth.example.com",
        expected_audience="urn:asap:agent:server",
    )

    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {
            "sub": "urn:asap:agent:test",
            "exp": now + 3600,
            "iss": "https://auth.example.com",
            "aud": ["urn:asap:agent:server", "other"],
        },
        key,
    )

    claims = await validator.validate_token(token)

    assert claims["sub"] == "urn:asap:agent:test"
