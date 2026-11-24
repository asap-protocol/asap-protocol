"""The agents that a trusted card key may sign for.

Each trust source declares its agent URNs. The scope is required: there is no
implicit "any agent", only the explicit opt-in ``ANY_AGENT``. A scope that is
easy to get wrong (a bare string, an empty list, a URN with a trailing
newline) is refused when the config is built, and the error never repeats a
key.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey
from pydantic import ValidationError

from asap.adapters.a2a.trust_scope import ANY_AGENT, AllowedJku, PinnedCardKey, scope_allows

_JWK = OKPKey.import_key(ed25519.Ed25519PrivateKey.generate().public_key()).as_dict()
_BANK_A = "urn:asap:agent:bank-a"
_BANK_B = "urn:asap:agent:bank-b"


@pytest.mark.parametrize(
    "agents",
    [[], _BANK_A, ["bank-a"], [f"{_BANK_A}\n"], [_BANK_A.upper()], ["*"], "*", "any-agent"],
    ids=[
        "empty list",
        "bare string",
        "not a URN",
        "trailing newline",
        "upper case",
        "wildcard in a list",
        "wildcard",
        "misspelled opt-in",
    ],
)
def test_a_scope_that_is_easy_to_get_wrong_is_refused(agents: Any) -> None:
    with pytest.raises(ValidationError, match="agents"):
        AllowedJku(agents=agents)
    with pytest.raises(ValidationError, match="agents"):
        PinnedCardKey(jwk=_JWK, agents=agents)


def test_the_scope_is_required() -> None:
    with pytest.raises(ValidationError, match="agents"):
        PinnedCardKey.model_validate({"jwk": _JWK})
    with pytest.raises(ValidationError, match="agents"):
        AllowedJku.model_validate({})


def test_a_scope_of_agent_urns_allows_exactly_those_agents() -> None:
    entry = PinnedCardKey(jwk=_JWK, agents=[_BANK_A])

    assert entry.agents == (_BANK_A,)
    assert scope_allows(entry.agents, _BANK_A)
    assert not scope_allows(entry.agents, _BANK_B)
    assert not scope_allows(entry.agents, f"{_BANK_A}\n")


def test_the_explicit_opt_in_allows_every_agent() -> None:
    entry = AllowedJku(agents=ANY_AGENT)

    assert entry.agents == "any_agent"
    assert scope_allows(entry.agents, _BANK_B)


def test_an_entry_error_never_repeats_the_key() -> None:
    secret = "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0"

    with pytest.raises(ValidationError) as raised:
        PinnedCardKey(jwk={**_JWK, "d": secret}, agents=[])

    assert secret not in str(raised.value)


def test_an_entry_is_frozen_and_forbids_unknown_fields() -> None:
    entry = AllowedJku(agents=[_BANK_A])

    with pytest.raises(ValidationError):
        entry.agents = ANY_AGENT
    with pytest.raises(ValidationError):
        AllowedJku.model_validate({"agents": [_BANK_A], "origins": ["https://a.example"]})
