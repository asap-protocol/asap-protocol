"""Pure mapping between an ASAP manifest and an A2A v1.0 Agent Card.

The functions here do no I/O and read no clock or global state. The field
table, with a reason for every unmapped field, is in
``docs/guides/interoperability.md`` ("Agent Card and manifest fields").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from asap.adapters.a2a._base import NonEmptyStr
from asap.adapters.a2a.errors import A2AMappingInputError
from asap.adapters.a2a.extension import AsapExtensionParams, SkillSchemas, build_asap_extension
from asap.adapters.a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from asap.models.base import ASAPBaseModel
from asap.models.entities import Manifest, Skill

_TagList = Annotated[list[NonEmptyStr], Field(min_length=1)]


class AgentCardInputs(ASAPBaseModel):
    """Card data that a manifest does not hold, for :func:`manifest_to_agent_card`.

    ``skill_tags`` needs an entry for every manifest skill: A2A requires a tag,
    and the mapping does not invent one. ``skill_names`` is optional; a skill
    without an entry is named by its ``id``.

    Example:
        >>> AgentCardInputs(interfaces=[interface], default_input_modes=["application/json"],
        ...     default_output_modes=["application/json"], skill_tags={"search": ["web"]})
    """

    interfaces: list[AgentInterface] = Field(min_length=1)
    default_input_modes: list[NonEmptyStr] = Field(min_length=1)
    default_output_modes: list[NonEmptyStr] = Field(min_length=1)
    skill_tags: dict[str, _TagList]
    skill_names: dict[str, NonEmptyStr] = Field(default_factory=dict)
    provider: AgentProvider | None = None
    documentation_url: NonEmptyStr | None = None
    icon_url: NonEmptyStr | None = None


@dataclass(frozen=True, slots=True)
class CardMapping:
    """Result of :func:`manifest_to_agent_card`."""

    card: AgentCard


def manifest_to_agent_card(manifest: Manifest, inputs: AgentCardInputs) -> CardMapping:
    """Build an A2A v1.0 Agent Card from an ASAP manifest.

    ASAP data that the card has no field for rides in the ASAP extension
    (:data:`asap.adapters.a2a.ASAP_EXTENSION_URI`). ``capabilities.streaming``
    is always written, so a card signature covers it.

    Raises:
        A2AMappingInputError: A skill has no tags.
        pydantic.ValidationError: The card would not be valid, for example
            because a required field would be empty.

    Example:
        >>> card = manifest_to_agent_card(manifest, inputs).card  # doctest: +SKIP
        >>> card.model_dump(mode="json")  # proto JSON form  # doctest: +SKIP
    """
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    tags = {skill_id: _tags_for(skill_id, inputs) for skill_id in skill_ids}
    card = AgentCard(
        name=manifest.name,
        description=manifest.description,
        supported_interfaces=inputs.interfaces,
        provider=inputs.provider,
        version=manifest.version,
        documentation_url=inputs.documentation_url,
        capabilities=AgentCapabilities(
            streaming=manifest.capabilities.streaming,
            extensions=[_asap_extension_for(manifest)],
        ),
        default_input_modes=inputs.default_input_modes,
        default_output_modes=inputs.default_output_modes,
        skills=[
            _card_skill(skill, tags[skill.id], inputs) for skill in manifest.capabilities.skills
        ],
        icon_url=inputs.icon_url,
    )
    return CardMapping(card=card)


def _asap_extension_for(manifest: Manifest) -> AgentExtension:
    schemas = {
        skill.id: SkillSchemas(input_schema=skill.input_schema, output_schema=skill.output_schema)
        for skill in manifest.capabilities.skills
        if skill.input_schema is not None or skill.output_schema is not None
    }
    params = AsapExtensionParams(
        agent_urn=manifest.id,
        asap_endpoint=manifest.endpoints.asap,
        events_endpoint=manifest.endpoints.events,
        asap_version=manifest.capabilities.asap_version,
        state_persistence=manifest.capabilities.state_persistence,
        skill_schemas=schemas or None,
    )
    return build_asap_extension(params)


def _card_skill(skill: Skill, tags: list[str], inputs: AgentCardInputs) -> AgentSkill:
    return AgentSkill(
        id=skill.id,
        name=inputs.skill_names.get(skill.id, skill.id),
        description=skill.description,
        tags=tags,
    )


def _tags_for(skill_id: str, inputs: AgentCardInputs) -> list[str]:
    tags = inputs.skill_tags.get(skill_id)
    if tags is None:
        raise A2AMappingInputError(
            "skill_tags",
            f"skill {skill_id!r} has no entry in skill_tags: an A2A skill needs at least "
            "one tag, and the mapping does not invent one",
        )
    return tags
