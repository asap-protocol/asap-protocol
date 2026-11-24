"""Claims of a trusted external token → principal (COMPAT-001).

``principal_from_claims`` runs after the signature (or introspection) check:
it checks ``aud``, ``exp``, ``nbf``, and ``sub``, and maps the grants.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.external_verifier import EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS, principal_from_claims
from tests.auth.local_issuer import LocalIssuer

_AUDIENCE = "https://api.example/asap"
_RULES = [{"scope": "files:read", "capability": "file.read"}]
_NOW = int(time.time())
_SKEW = EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS
Reason = ExternalTokenErrorReason
_VECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "interop"
    / "external-token-numeric-dates.json"
)
_NUMERIC_DATE_VECTORS: dict[str, Any] = json.loads(_VECTOR_PATH.read_text(encoding="utf-8"))
_NON_FINITE_DATES = {
    "positive_infinity": float("inf"),
    "negative_infinity": -float("inf"),
    "nan": float("nan"),
}


@pytest.fixture
def idp() -> LocalIssuer:
    return LocalIssuer("https://idp.example/")


def _issuer(idp: LocalIssuer) -> ExternalIssuerConfig:
    return ExternalIssuerConfig.model_validate(idp.config_entry(_AUDIENCE, grant_rules=_RULES))


def test_trusted_claims_give_a_principal_with_mapped_grants(idp: LocalIssuer) -> None:
    claims = idp.claims(_AUDIENCE, subject="user-42", scope="files:read admin")
    principal = principal_from_claims(_issuer(idp), claims)

    assert principal.key == ("https://idp.example/", "user-42")
    assert principal.client_id == "test-client"
    assert principal.scopes == ("admin", "files:read")
    assert principal.expires_at == datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    assert [(g.capability, g.status) for g in principal.grants] == [("file.read", "active")]


def test_audience_given_as_a_list_works(idp: LocalIssuer) -> None:
    claims = idp.claims(["https://other.example", _AUDIENCE])
    assert principal_from_claims(_issuer(idp), claims).subject == "user-1"


def test_client_id_falls_back_to_azp(idp: LocalIssuer) -> None:
    claims = idp.claims(_AUDIENCE, client_id=None, azp="mcp-client")
    assert principal_from_claims(_issuer(idp), claims).client_id == "mcp-client"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "https://other.example"}, Reason.AUDIENCE_MISMATCH),
        ({"aud": ["https://other.example"]}, Reason.AUDIENCE_MISMATCH),
        ({"aud": None}, Reason.AUDIENCE_MISMATCH),
        ({"exp": _NOW - _SKEW - 60}, Reason.TOKEN_EXPIRED),
        ({"nbf": _NOW + _SKEW + 60}, Reason.TOKEN_NOT_YET_VALID),
        ({"exp": None}, Reason.MISSING_CLAIM),
        ({"sub": None}, Reason.MISSING_CLAIM),
        ({"sub": "  "}, Reason.MISSING_CLAIM),
        ({"sub": 42}, Reason.MISSING_CLAIM),
        ({"exp": float("nan")}, Reason.MALFORMED_TOKEN),
        ({"exp": True}, Reason.MALFORMED_TOKEN),
        ({"exp": "2030-01-01"}, Reason.MALFORMED_TOKEN),
        ({"exp": 10**20}, Reason.MALFORMED_TOKEN),
        ({"exp": 10**400}, Reason.MALFORMED_TOKEN),
        ({"nbf": float("inf")}, Reason.MALFORMED_TOKEN),
        ({"nbf": -(10**400)}, Reason.MALFORMED_TOKEN),
    ],
)
def test_refused_claims_raise_a_typed_error(
    idp: LocalIssuer, overrides: dict[str, Any], reason: Reason
) -> None:
    with pytest.raises(ExternalTokenError) as caught:
        principal_from_claims(_issuer(idp), idp.claims(_AUDIENCE, **overrides))
    assert caught.value.reason is reason


def test_small_clock_skew_is_tolerated(idp: LocalIssuer) -> None:
    claims = idp.claims(_AUDIENCE, exp=_NOW - _SKEW // 2, nbf=_NOW + _SKEW // 2)
    assert principal_from_claims(_issuer(idp), claims).subject == "user-1"


def test_unmapped_scope_grants_nothing(idp: LocalIssuer) -> None:
    claims = idp.claims(_AUDIENCE, scope="files:write admin")
    assert principal_from_claims(_issuer(idp), claims).grants == ()


@pytest.mark.parametrize(
    "vector", _NUMERIC_DATE_VECTORS["vectors"], ids=lambda vector: vector["name"]
)
def test_numeric_date_vector(
    idp: LocalIssuer, monkeypatch: pytest.MonkeyPatch, vector: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        "asap.auth.external_verifier.time.time", lambda: _NUMERIC_DATE_VECTORS["now"]
    )
    dates = {
        name: _NON_FINITE_DATES[value] if isinstance(value, str) else value
        for name, value in vector["claims"].items()
    }
    claims = idp.claims(_AUDIENCE, **dates)
    issuer = _issuer(idp)

    if vector["expected_exp"] is not None:
        assert (
            principal_from_claims(issuer, claims).expires_at.timestamp() == vector["expected_exp"]
        )
        return

    with pytest.raises(ExternalTokenError) as caught:
        principal_from_claims(issuer, claims)
    assert caught.value.reason is Reason.MALFORMED_TOKEN
