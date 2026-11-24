"""External-token issuer config (COMPAT-001)."""

from __future__ import annotations

from typing import Any

import pytest
from joserfc import jwk
from pydantic import ValidationError

from asap.auth.external_tokens import ExternalIssuerConfig, ExternalTokenConfig

_ISSUER = "https://issuer.example"
_AUDIENCE = "https://api.example/asap"


def _public_jwks() -> dict[str, Any]:
    key = jwk.OKPKey.generate_key("Ed25519", private=True)
    return {"keys": [key.as_dict(private=False)]}


def _issuer(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "issuer": _ISSUER,
        "audience": _AUDIENCE,
        "key_source": {"kind": "jwks_uri", "jwks_uri": f"{_ISSUER}/jwks.json"},
    }
    entry.update(overrides)
    return entry


def test_empty_issuer_list_is_an_error() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ExternalTokenConfig.model_validate({"issuers": []})


@pytest.mark.parametrize("audience", [None, "", "  "])
def test_missing_or_blank_audience_is_an_error(audience: str | None) -> None:
    entry = _issuer(audience=audience)
    if audience is None:
        del entry["audience"]
    with pytest.raises(ValidationError, match="audience"):
        ExternalTokenConfig.model_validate({"issuers": [entry]})


def test_duplicate_issuers_are_an_error() -> None:
    issuers = [_issuer(), _issuer(issuer=f"{_ISSUER}/")]
    with pytest.raises(ValidationError, match="duplicate issuer"):
        ExternalTokenConfig.model_validate({"issuers": issuers})


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "http://issuer.example"},
        {"issuer": "https://10.1.2.3"},
        {"issuer": "https://localhost:8443"},
        {"key_source": {"kind": "jwks_uri", "jwks_uri": "https://169.254.169.254/jwks"}},
        {"key_source": None, "introspection": {"url": "http://issuer.example/introspect"}},
    ],
)
def test_url_that_fails_the_guard_is_an_error(overrides: dict[str, Any]) -> None:
    if "introspection" in overrides:
        overrides["introspection"].update(client_id="rs", client_secret="s3cret")
    with pytest.raises(ValidationError, match="guarded fetch"):
        ExternalIssuerConfig.model_validate(_issuer(**overrides))


def test_switch_allows_a_local_issuer() -> None:
    entry = _issuer(issuer="https://localhost:8443", allow_private_hosts=True)
    assert ExternalIssuerConfig.model_validate(entry).issuer == "https://localhost:8443"


def test_static_jwks_needs_no_fetch() -> None:
    jwks = _public_jwks()
    entry = _issuer(key_source={"kind": "static_jwks", "jwks": jwks})
    source = ExternalIssuerConfig.model_validate(entry).key_source
    assert source is not None and source.kind == "static_jwks"
    assert [key.as_dict()["x"] for key in source.key_set().keys] == [jwks["keys"][0]["x"]]


@pytest.mark.parametrize(
    "jwks",
    [
        {"keys": [jwk.OKPKey.generate_key("Ed25519", private=True).as_dict(private=True)]},
        {"keys": [{"kty": "oct", "k": "c2VjcmV0LWtleS1mb3ItaG1hYy1vbmx5"}]},
        {"keys": []},
        {"not_keys": []},
    ],
)
def test_static_jwks_holds_only_public_asymmetric_keys(jwks: dict[str, Any]) -> None:
    entry = _issuer(key_source={"kind": "static_jwks", "jwks": jwks})
    with pytest.raises(ValidationError, match="static_jwks"):
        ExternalIssuerConfig.model_validate(entry)


def test_issuer_needs_exactly_one_of_key_source_and_introspection() -> None:
    introspection = {"url": f"{_ISSUER}/introspect", "client_id": "rs", "client_secret": "s"}
    for entry in (_issuer(key_source=None), _issuer(introspection=introspection)):
        with pytest.raises(ValidationError, match="exactly one of key_source and introspection"):
            ExternalIssuerConfig.model_validate(entry)


def test_at_most_one_issuer_uses_introspection() -> None:
    def introspected(issuer: str) -> dict[str, Any]:
        endpoint = {"url": f"{issuer}/introspect", "client_id": "rs", "client_secret": "s"}
        return _issuer(issuer=issuer, key_source=None, introspection=endpoint)

    issuers = [introspected(_ISSUER), introspected("https://other.example")]
    with pytest.raises(ValidationError, match="at most one issuer"):
        ExternalTokenConfig.model_validate({"issuers": issuers})


def test_unknown_field_is_an_error() -> None:
    with pytest.raises(ValidationError, match="accept_any_issuer"):
        ExternalIssuerConfig.model_validate(_issuer(accept_any_issuer=True))


def test_issuer_lookup_is_exact_up_to_a_trailing_slash() -> None:
    config = ExternalTokenConfig.model_validate({"issuers": [_issuer()]})
    assert config.issuer_for(f"{_ISSUER}/") is config.issuers[0]
    assert config.issuer_for("https://issuer.example.evil") is None
    assert config.issuer_for("https://ISSUER.example") is None


def test_introspection_cache_ttl_defaults_to_60_and_is_bounded() -> None:
    endpoint = {"url": f"{_ISSUER}/introspect", "client_id": "rs", "client_secret": "s"}
    entry = ExternalIssuerConfig.model_validate(_issuer(key_source=None, introspection=endpoint))
    assert entry.introspection is not None and entry.introspection.cache_ttl_seconds == 60
    for ttl in (0, 3600):
        bounded = {**endpoint, "cache_ttl_seconds": ttl}
        ExternalIssuerConfig.model_validate(_issuer(key_source=None, introspection=bounded))
    for ttl in (-1, 3601):
        with pytest.raises(ValidationError, match="cache_ttl_seconds"):
            unbounded = {**endpoint, "cache_ttl_seconds": ttl}
            ExternalIssuerConfig.model_validate(_issuer(key_source=None, introspection=unbounded))


def test_client_secret_is_not_in_the_repr() -> None:
    endpoint = {"url": f"{_ISSUER}/introspect", "client_id": "rs", "client_secret": "s3cret"}
    entry = ExternalIssuerConfig.model_validate(_issuer(key_source=None, introspection=endpoint))
    assert "s3cret" not in repr(entry)
