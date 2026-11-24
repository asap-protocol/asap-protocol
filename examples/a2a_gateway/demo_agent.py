"""The demo ASAP agent behind the gateway: its manifest, card inputs, grant rules, and skills.

Two skills, so a caller must select one:
``echo`` returns its text; ``greet`` greets a name in English or Portuguese,
and its grant carries a constraint on ``language``. The skill ``id`` is the
capability name in the grant rules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from asap.adapters.a2a import AgentCardInputs, AgentInterface
from asap.models.entities import Capability, Endpoint, Manifest, Skill

A2A_PATH = "/a2a"
"""Path of the A2A JSON-RPC interface on the gateway origin."""

DEMO_AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
"""The demo agent's URN: a fixed value, so a checker can pin the card key for it."""

SkillHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_TEXT_INPUT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
_GREET_INPUT = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "language": {"enum": ["en", "pt", "fr"]}},
    "required": ["name", "language"],
}

DEMO_GRANT_RULES: tuple[dict[str, Any], ...] = (
    {"scope": "demo:echo", "capability": "echo"},
    {
        "scope": "demo:greet",
        "capability": "greet",
        "constraints": {"language": {"in": ["en", "pt"]}},
    },
)
"""Static grant rules for the demo issuer: one scope per skill (COMPAT-002)."""


def demo_manifest(origin: str) -> Manifest:
    """The ASAP manifest of the demo agent served at *origin*."""
    return Manifest(
        id=DEMO_AGENT_URN,
        name="A2A Gateway Demo",
        version="1.0.0",
        description="An ASAP agent reached through a reference A2A v1.0 gateway",
        capabilities=Capability(
            skills=[
                Skill(
                    id="echo", description="Return the text it receives", input_schema=_TEXT_INPUT
                ),
                Skill(id="greet", description="Greet a person by name", input_schema=_GREET_INPUT),
            ]
        ),
        endpoints=Endpoint(asap=f"{origin}/asap"),
    )


def demo_card_inputs(origin: str) -> AgentCardInputs:
    """The card data a manifest does not hold; the card security comes from the token config."""
    return AgentCardInputs(
        interfaces=[
            AgentInterface(
                url=f"{origin}{A2A_PATH}", protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skill_tags={"echo": ["demo"], "greet": ["demo", "greeting"]},
        skill_names={"echo": "Echo", "greet": "Greet"},
    )


async def echo(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the ``text`` argument."""
    return {"text": arguments.get("text")}


async def greet(arguments: dict[str, Any]) -> dict[str, Any]:
    """Greet ``name`` in ``language`` (``en`` or ``pt``)."""
    salutation = "Olá" if arguments.get("language") == "pt" else "Hello"
    return {"greeting": f"{salutation}, {arguments.get('name')}!"}


DEMO_SKILLS: dict[str, SkillHandler] = {"echo": echo, "greet": greet}
