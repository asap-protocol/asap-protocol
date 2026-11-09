"""Lock OAuth audience intersection to exact strings."""

from __future__ import annotations

import pytest

from asap.auth.jwks import audience_matches_expected

_AUDIENCE = "urn:asap:agent:bot-01"
_NEIGHBORS = (
    "urn:asap:agent:bot-010",
    "urn:asap:agent:bot-0",
    "urn:asap:agent:bot",
    "URN:asap:agent:bot-01",
    " urn:asap:agent:bot-01",
    "urn:asap:agent:bot-01 ",
)


@pytest.mark.parametrize("audience", _NEIGHBORS)
def test_audience_match_is_exact(audience: str) -> None:
    """A prefix, case change, or padded copy is not the expected audience."""
    assert audience_matches_expected({"aud": _AUDIENCE}, _AUDIENCE) is True
    assert audience_matches_expected({"aud": [_AUDIENCE]}, [_AUDIENCE]) is True
    assert audience_matches_expected({"aud": audience}, _AUDIENCE) is False
    assert audience_matches_expected({"aud": [audience]}, [_AUDIENCE]) is False
    assert audience_matches_expected({"aud": _AUDIENCE}, [audience]) is False
