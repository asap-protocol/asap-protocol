"""Discovery route for the A2A v1.0 Agent Card (COMPAT-003, v2.6.0).

``create_app(manifest, agent_card=...)`` serves the card at
``/.well-known/agent-card.json`` beside the ASAP manifest, from the same
origin. The card is checked once, when the app is built: it must carry a
signature and describe the agent of the manifest (same name and version, and
the URN of its ASAP extension equals the manifest ``id``). Build it with
:func:`asap.adapters.a2a.build_signed_agent_card`; a card that fails to sign
never exists, so it is never served. Without a card the route is not mounted.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from asap.adapters.a2a import ASAP_EXTENSION_URI, AgentCard, read_asap_extension
from asap.discovery import wellknown
from asap.models.entities import Manifest


def validate_served_agent_card(card: AgentCard, manifest: Manifest) -> None:
    """Raise ``ValueError`` unless *card* is signed and describes the agent of *manifest*.

    Example:
        >>> validate_served_agent_card(signed_card, manifest)  # doctest: +SKIP
    """
    if not card.signatures:
        raise ValueError(
            f"agent_card {card.name!r} has no signature; build it with "
            "asap.adapters.a2a.build_signed_agent_card"
        )
    extension = read_asap_extension(card)
    if extension is None:
        raise ValueError(
            f"agent_card {card.name!r} has no ASAP extension ({ASAP_EXTENSION_URI}), so it "
            "cannot name the agent of the manifest"
        )
    pairs = {
        "agent_urn": (extension.agent_urn, manifest.id),
        "name": (card.name, manifest.name),
        "version": (card.version, manifest.version),
    }
    mismatched = [
        f"{field} {got!r} != {want!r}" for field, (got, want) in pairs.items() if got != want
    ]
    if mismatched:
        raise ValueError(
            f"agent_card describes another agent than the manifest: {'; '.join(mismatched)}"
        )


def create_agent_card_router() -> APIRouter:
    """Return the router for ``GET /.well-known/agent-card.json``.

    It reads the card from ``app.state.agent_card``, which ``create_app`` sets
    after :func:`validate_served_agent_card`.
    """
    router = APIRouter(tags=["discovery"])

    @router.get(wellknown.WELLKNOWN_AGENT_CARD_PATH)
    async def get_agent_card(request: Request) -> Response:
        """Return the signed A2A Agent Card of this agent."""
        card: AgentCard = request.app.state.agent_card
        return await wellknown.get_agent_card_response(card, request)

    return router


__all__ = ["create_agent_card_router", "validate_served_agent_card"]
