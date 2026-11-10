"""Unit tests for OIDC discovery and OIDC + JWKS integration."""

import time

import httpx
import pytest
from joserfc import jwk
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError

from asap.auth.jwks import JWKSValidator
from asap.auth.oidc import OIDCDiscovery, _validate_issuer_url


def _discovery_response(jwks_uri: str = "https://auth.example.com/.well-known/jwks.json") -> dict:
    """Minimal valid OIDC discovery document."""
    return {
        "issuer": "https://auth.example.com",
        "token_endpoint": "https://auth.example.com/oauth/token",
        "jwks_uri": jwks_uri,
        "scopes_supported": ["openid", "asap:execute"],
    }


def _make_jwks_response(public_key: jwk.RSAKey) -> dict:
    """Build JWKS JSON response from RSA key."""
    return {"keys": [public_key.as_dict(private=False)]}


def test_validate_issuer_url_blocks_private_hosts() -> None:
    """Verify private/internal issuer_url hosts are rejected by default."""
    with pytest.raises(ValueError, match="private/internal"):
        _validate_issuer_url("http://127.0.0.1/")
    with pytest.raises(ValueError, match="private/internal"):
        _validate_issuer_url("https://localhost/.well-known/openid-configuration")
    with pytest.raises(ValueError, match="private/internal"):
        _validate_issuer_url("http://192.168.1.1/")


@pytest.mark.parametrize(
    "issuer_url",
    [
        "http://[::1]/",
        "https://[::]/",
        "https://[fe80::1]/",
        "https://[fea0::1]/.well-known/openid-configuration",
        "http://169.254.169.254/",
        "http://240.0.0.1/",
        "http://0.0.0.1/",
        "http://[fc00::1]/",
        "http://127.0.0.2/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_validate_issuer_url_blocks_reserved_and_link_local_literals(issuer_url: str) -> None:
    """Discovery must reject unspecified, fe80::/10, ULA, and Class E issuer hosts."""
    with pytest.raises(ValueError, match="private/internal"):
        _validate_issuer_url(issuer_url)


def test_validate_issuer_url_allows_public_hosts() -> None:
    """Verify public issuer_url hosts are allowed."""
    _validate_issuer_url("https://auth.example.com")
    _validate_issuer_url("https://tenant.auth0.com")


def test_validate_issuer_url_allow_private_issuers_override() -> None:
    """Verify allow_private_issuers=True permits localhost for dev."""
    _validate_issuer_url("http://127.0.0.1/", allow_private_issuers=True)
    _validate_issuer_url("https://localhost/", allow_private_issuers=True)


def test_oidc_discovery_rejects_private_issuer_by_default() -> None:
    """Verify OIDCDiscovery raises on private issuer_url unless allow_private_issuers=True."""
    with pytest.raises(ValueError, match="private/internal"):
        OIDCDiscovery("http://127.0.0.1/")


def test_oidc_discovery_rejects_fe80_slash10_issuer() -> None:
    """fea0::/10 is link-local; a prefix check that only matches fe80: would miss it."""
    with pytest.raises(ValueError, match="fea0::1"):
        OIDCDiscovery("https://[fea0::1]/")


async def test_oidc_discovery_with_allow_private_issuers() -> None:
    """Verify OIDCDiscovery with allow_private_issuers=True works for localhost."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_discovery_response())

    discovery = OIDCDiscovery(
        "http://127.0.0.1",
        transport=httpx.MockTransport(mock_handler),
        allow_private_issuers=True,
    )
    config = await discovery.discover()
    assert config.issuer == "https://auth.example.com"


async def test_discover_fetches_and_parses_config() -> None:
    """Verify discover() fetches and parses OIDC config from well-known URL."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_discovery_response())

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )
    config = await discovery.discover()

    assert config.issuer == "https://auth.example.com"
    assert config.token_endpoint == "https://auth.example.com/oauth/token"
    assert config.jwks_uri == "https://auth.example.com/.well-known/jwks.json"
    assert "openid" in config.scopes_supported
    assert "asap:execute" in config.scopes_supported
    assert call_count == 1


async def test_discover_second_call_uses_cache() -> None:
    """Verify second discover() uses cache (no extra HTTP call)."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_discovery_response())

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )

    config_1 = await discovery.discover()
    config_2 = await discovery.discover()

    assert config_1.issuer == config_2.issuer
    assert config_1 is config_2
    assert call_count == 1


async def test_discover_raises_on_missing_issuer() -> None:
    """Verify discover() raises ValueError when issuer is missing."""
    data = _discovery_response()
    del data["issuer"]

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(ValueError, match="issuer"):
        await discovery.discover()


async def test_discover_raises_on_http_error() -> None:
    """Verify discover() raises on HTTP error."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await discovery.discover()


async def test_oidc_jwks_integration_jwt_validation_with_discovered_jwks_uri() -> None:
    """Verify JWT validation using OIDC-discovered jwks_uri (OIDC + JWKS integration)."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(key)
    jwks_url = "https://auth.example.com/.well-known/jwks.json"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "openid-configuration" in str(request.url) or request.url.path.endswith(
            "openid-configuration"
        ):
            return httpx.Response(
                200,
                json=_discovery_response(jwks_uri=jwks_url),
            )
        if "jwks" in str(request.url) or "jwks.json" in str(request.url):
            return httpx.Response(200, json=jwks_json)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    discovery = OIDCDiscovery("https://auth.example.com", transport=transport)
    config = await discovery.discover()

    validator = JWKSValidator(config.jwks_uri, transport=transport)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "urn:asap:agent:test", "scope": "asap:execute", "exp": now + 3600},
        key,
    )

    claims = await validator.validate_token(token)

    assert claims["sub"] == "urn:asap:agent:test"
    assert "asap:execute" in (claims.get("scope") or "").split()


async def test_oidc_jwks_integration_rejects_invalid_token() -> None:
    """Verify invalid token is rejected when validating with OIDC-discovered jwks_uri."""
    key = jwk.RSAKey.generate_key(2048, private=True)
    other_key = jwk.RSAKey.generate_key(2048, private=True)
    jwks_json = _make_jwks_response(other_key)
    jwks_url = "https://auth.example.com/.well-known/jwks.json"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "openid-configuration" in str(request.url) or request.url.path.endswith(
            "openid-configuration"
        ):
            return httpx.Response(
                200,
                json=_discovery_response(jwks_uri=jwks_url),
            )
        if "jwks" in str(request.url) or "jwks.json" in str(request.url):
            return httpx.Response(200, json=jwks_json)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    discovery = OIDCDiscovery("https://auth.example.com", transport=transport)
    config = await discovery.discover()

    validator = JWKSValidator(config.jwks_uri, transport=transport)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": "RS256"},
        {"sub": "urn:asap:agent:test", "exp": now + 3600},
        key,
    )

    with pytest.raises(JoseError):
        await validator.validate_token(token)


@pytest.mark.parametrize("field", ["issuer", "token_endpoint", "jwks_uri"])
@pytest.mark.parametrize("value", ["", 12, ["https://auth.example.com"]])
async def test_discover_rejects_blank_or_non_string_required_fields(
    field: str,
    value: object,
) -> None:
    """Empty and non-string discovery fields use the missing-required error.

    ``""`` is a string, so dropping the emptiness check accepts it. ``12`` and a
    list are truthy, so dropping the string check lets Pydantic raise instead.
    """
    document = _discovery_response()
    document[field] = value

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(ValueError, match=f"missing required '{field}'"):
        await discovery.discover()


@pytest.mark.parametrize(
    "scopes",
    ["openid asap:execute", True, {"openid": True}],
)
async def test_discover_drops_non_list_scopes_supported(scopes: object) -> None:
    """A string, bool, or object of scopes does not become the supported list.

    Iterating a string would advertise one scope per character.
    """
    document = _discovery_response()
    document["scopes_supported"] = scopes

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=document)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )
    config = await discovery.discover()

    assert config.scopes_supported == []


async def test_oidc_discover_raises_on_missing_jwks_uri() -> None:
    """Verify discover() raises ValueError when jwks_uri is missing."""
    data = _discovery_response()
    del data["jwks_uri"]

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    discovery = OIDCDiscovery(
        "https://auth.example.com",
        transport=httpx.MockTransport(mock_handler),
    )

    with pytest.raises(ValueError, match="jwks_uri"):
        await discovery.discover()
