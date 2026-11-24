"""The reference A2A v1.0 gateway: the A2A flow of v2.6.0.

:func:`create_gateway_app` builds one FastAPI app. ``create_app`` from
``asap-protocol`` serves the ASAP manifest and, beside it, the signed Agent
Card from :func:`asap.adapters.a2a.build_signed_agent_card` (COMPAT-003). The
card security states what the external-token config enforces.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import FastAPI

from asap.adapters.a2a import AgentCardInputs, CardSigner, build_signed_agent_card
from asap.auth.external_tokens import ExternalTokenConfig
from asap.models.entities import Manifest
from asap.transport.server import create_app
from examples.a2a_gateway.demo_agent import SkillHandler


def create_gateway_app(
    manifest: Manifest,
    card_inputs: AgentCardInputs,
    *,
    signers: Sequence[CardSigner],
    external_tokens: ExternalTokenConfig,
    skills: Mapping[str, SkillHandler],
) -> FastAPI:
    """Return the gateway app for *manifest*, whose skills run as *skills*.

    Raises:
        ValueError: *skills* does not hold exactly the manifest's skills.
        asap.adapters.a2a.A2AAdapterError: The card cannot be built or signed.
    """
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    if sorted(skills) != sorted(skill_ids):
        raise ValueError(
            f"skills {sorted(skills)} must match the manifest skills {sorted(skill_ids)}"
        )
    card = build_signed_agent_card(
        manifest, card_inputs, signers=signers, external_tokens=external_tokens
    ).card
    app: FastAPI = create_app(manifest, agent_card=card)
    return app
