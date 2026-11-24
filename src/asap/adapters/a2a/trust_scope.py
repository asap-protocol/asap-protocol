"""Which agents a trusted card key may sign for.

A signature that verifies proves only that the holder of the key signed the
card, not that the card's agent URN belongs to that holder. So every trust
source in :class:`asap.adapters.a2a.A2ATrustConfig` names the agent URNs that
its keys may sign for, and ingest accepts a card only when its agent is in
the scope of a signer that verified. The scope is required: an entry without
``agents`` is refused, and "any agent" exists only as the explicit opt-in
:data:`ANY_AGENT`.

In a config file::

    pinned_keys:
      bank-a-card-1:
        jwk: {kty: OKP, crv: Ed25519, x: "..."}
        agents: [urn:asap:agent:bank-a]
    jku_allowlist:
      https://keys.bank-b.example/jwks.json:
        agents: [urn:asap:agent:bank-b, urn:asap:agent:bank-b:payments]
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import ConfigDict, field_validator

from asap.adapters.a2a.errors import quoted_prefix
from asap.models.base import ASAPBaseModel
from asap.models.constants import AGENT_URN_PATTERN, MAX_URN_LENGTH

ANY_AGENT: Final = "any_agent"
"""Explicit opt-in: the keys of this source may sign a card for **every** agent URN.

Whoever holds such a key, or controls such a ``jku`` host, can make a card
that verifies for any agent. Use it only for a source that you trust as much
as every agent you will ever ingest, such as your own registry signing key.
"""

AgentScope = tuple[str, ...] | Literal["any_agent"]
"""The agent URNs that a trust source may sign for, or :data:`ANY_AGENT`."""

# fullmatch, not match: "$" in AGENT_URN_PATTERN accepts a trailing newline.
_AGENT_URN = re.compile(AGENT_URN_PATTERN)


def scope_allows(agents: AgentScope, agent_urn: str) -> bool:
    """Return True if a source scoped to *agents* may sign a card for *agent_urn*.

    Example:
        >>> scope_allows(("urn:asap:agent:bank-a",), "urn:asap:agent:bank-b")
        False
    """
    return agents == ANY_AGENT or agent_urn in agents


def _checked_scope(agents: AgentScope) -> AgentScope:
    if agents == ANY_AGENT:
        return agents
    if not agents:
        raise ValueError(f"agents must list at least one agent URN, or be {ANY_AGENT!r}")
    for urn in agents:
        if len(urn) > MAX_URN_LENGTH or not _AGENT_URN.fullmatch(urn):
            raise ValueError(
                f"agents holds {quoted_prefix(urn)}, which is not an agent URN "
                "(urn:asap:agent:<name>, lower case)"
            )
    return agents


class PinnedCardKey(ASAPBaseModel):
    """One pinned card key: its public JWK and the agents it may sign for.

    Example:
        >>> PinnedCardKey(jwk=public_jwk, agents=["urn:asap:agent:bank-a"])  # doctest: +SKIP
    """

    # A validation error must not echo the JWK: a mistaken one may hold private members.
    model_config = ConfigDict(hide_input_in_errors=True)

    jwk: dict[str, Any]
    agents: AgentScope

    @field_validator("agents")
    @classmethod
    def _check_agents(cls, agents: AgentScope) -> AgentScope:
        return _checked_scope(agents)


class AllowedJku(ASAPBaseModel):
    """One allowed ``jku``: the agents that the keys of its JWKS may sign for.

    Example:
        >>> AllowedJku(agents=["urn:asap:agent:bank-b"]).agents
        ('urn:asap:agent:bank-b',)
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    agents: AgentScope

    @field_validator("agents")
    @classmethod
    def _check_agents(cls, agents: AgentScope) -> AgentScope:
        return _checked_scope(agents)
