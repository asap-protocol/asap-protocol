"""``oauth2_config`` and the external-token config together.

With both set, ``POST /asap/capability/execute`` owns its bearer: the
middleware skips that exact path, and the route verifies the token once,
on the Agent JWT path or the external path. Every other ``/asap`` path stays
behind the middleware, with or without the external config. To accept the
``oauth2_config`` IdP on execute, list it as an external issuer, with
``required_claims`` for the identity binding.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from joserfc import jwk

from asap.auth import OAuth2Config
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM
from tests.auth.local_issuer import LocalIssuer
from tests.transport.test_capability_execute_unchanged import EXPECTED_AGENT_PATH, execute_case
from tests.transport.test_capability_external_tokens import _AUDIENCE, _App

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_IDP = LocalIssuer("https://idp.example")
_PARTNER = LocalIssuer("https://partner.example")
_MIDDLEWARE_REFUSAL = (401, {"detail": "Invalid authentication token"})


async def _idp_keys(uri: str) -> jwk.KeySet:
    return jwk.KeySet.import_key_set(_IDP.jwks)


def _server(
    manifest: Manifest, limiter: ASAPRateLimiter | None, *issuers: LocalIssuer, **oauth2: Any
) -> _App:
    """``oauth2`` adds ``OAuth2Config`` fields, such as ``required_scope``."""
    config = OAuth2Config(jwks_uri="https://idp.example/jwks", jwks_fetcher=_idp_keys, **oauth2)
    return _App(manifest, limiter, *issuers, oauth2_config=config)


@pytest.fixture
def both(sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None) -> _App:
    return _server(sample_manifest, isolated_rate_limiter, _IDP, _PARTNER)


def _token(issuer: LocalIssuer) -> str:
    return issuer.mint(issuer.claims(_AUDIENCE, scope="files:read"))


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", list(EXPECTED_AGENT_PATH))
async def test_agent_jwt_on_execute_is_verified_by_the_route(both: _App, name: str) -> None:
    actual = await execute_case(both.client, both.app, both.agent_store, both.registry, name)
    assert actual == EXPECTED_AGENT_PATH[name]


@pytest.mark.parametrize("issuer", [_IDP, _PARTNER], ids=["oauth2 idp", "partner"])
def test_listed_issuers_execute_through_the_route(both: _App, issuer: LocalIssuer) -> None:
    response = both.execute(_token(issuer), {"capability": "file:read"})
    assert response.status_code == 200
    assert response.json()["principal"]["issuer"] == issuer.issuer


def test_idp_token_not_listed_is_refused_by_the_route(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None
) -> None:
    server = _server(sample_manifest, isolated_rate_limiter, _PARTNER)
    response = server.execute(_token(_IDP), {"capability": "file:read"})
    assert (response.status_code, response.json()["detail"]) == (401, "invalid_token")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/asap/capability/list"),
        ("POST", "/asap/capability/execute/"),
        ("POST", "/asap/capability//execute"),
        ("POST", "/asap"),
    ],
)
@pytest.mark.parametrize("external_config", [True, False], ids=["both configs", "oauth2 only"])
def test_other_asap_paths_stay_behind_the_middleware(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    external_config: bool,
    method: str,
    path: str,
) -> None:
    issuers = (_IDP, _PARTNER) if external_config else ()
    server = _server(sample_manifest, isolated_rate_limiter, *issuers)
    for token, expected in [
        (_token(_PARTNER), _MIDDLEWARE_REFUSAL),
        (None, (401, {"detail": "Authentication required"})),
    ]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = server.client.request(method, path, headers=headers, json={})
        assert (response.status_code, response.json()) == expected, (path, token is None)


# One IdP serves agents X and Y with one audience.
# On execute the middleware's custom-claim binding does not run, so the listed
# IdP entry must carry it as required_claims, or X's token executes on Y.
_OTHER_AGENT = "urn:asap:agent:x"
_UNBOUND_EVENT = "asap.server.external_issuer_unbound"


def _bound(manifest: Manifest, limiter: ASAPRateLimiter | None) -> _App:
    oauth2 = OAuth2Config(
        jwks_uri="https://idp.example/jwks", jwks_fetcher=_idp_keys, expected_issuer=_IDP.issuer
    )
    required = {"required_claims": {DEFAULT_CUSTOM_CLAIM: manifest.id}}
    return _App(manifest, limiter, _IDP, entry_overrides=required, oauth2_config=oauth2)


@pytest.mark.parametrize(
    ("agent_claim", "status"),
    [
        (lambda me: me, 200),
        (lambda me: [_OTHER_AGENT, me], 200),
        (lambda me: _OTHER_AGENT, 401),
        (lambda me: None, 401),
    ],
    ids=["this agent", "list holding this agent", "another agent", "no agent claim"],
)
def test_required_claims_restore_the_identity_binding_on_execute(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    agent_claim: Callable[[str], str | list[str] | None],
    status: int,
) -> None:
    server = _bound(sample_manifest, isolated_rate_limiter)
    value = agent_claim(sample_manifest.id)
    extra = {} if value is None else {DEFAULT_CUSTOM_CLAIM: value}
    token = _IDP.mint(_IDP.claims(_AUDIENCE, scope="files:read", **extra))

    response = server.execute(token, {"capability": "file:read"})
    assert response.status_code == status
    if status == 401:
        assert response.json()["detail"] == "invalid_token"


def _unbound_warnings(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        r.msg for r in caplog.records
        if isinstance(r.msg, dict) and r.msg.get("event") == _UNBOUND_EVENT
    ]  # fmt: skip


def test_startup_does_not_warn_when_the_listed_idp_is_bound(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        _bound(sample_manifest, isolated_rate_limiter)
    assert _unbound_warnings(caplog) == []


@pytest.mark.parametrize("matched_by", ["expected_issuer", "jwks_uri"])
def test_startup_warns_when_the_listed_idp_is_unbound(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    caplog: pytest.LogCaptureFixture,
    matched_by: str,
) -> None:
    jwks_uri = "https://idp.example/jwks"
    if matched_by == "expected_issuer":
        oauth2 = OAuth2Config(
            jwks_uri=jwks_uri, jwks_fetcher=_idp_keys, expected_issuer=_IDP.issuer
        )
        overrides = None
    else:
        oauth2 = OAuth2Config(jwks_uri=jwks_uri, jwks_fetcher=_idp_keys)
        overrides = {"key_source": {"kind": "jwks_uri", "jwks_uri": jwks_uri}}
    with caplog.at_level("WARNING"):
        _App(
            sample_manifest,
            isolated_rate_limiter,
            _IDP,
            entry_overrides=overrides,
            oauth2_config=oauth2,
        )

    [warning] = _unbound_warnings(caplog)
    assert warning["issuer"] == _IDP.issuer
    assert warning["required_claims"] == {DEFAULT_CUSTOM_CLAIM: sample_manifest.id}


def test_startup_does_not_warn_for_issuers_other_than_the_idp(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    oauth2 = OAuth2Config(
        jwks_uri="https://idp.example/jwks", jwks_fetcher=_idp_keys, expected_issuer=_IDP.issuer
    )
    with caplog.at_level("WARNING"):
        _App(sample_manifest, isolated_rate_limiter, _PARTNER, oauth2_config=oauth2)
    assert _unbound_warnings(caplog) == []
