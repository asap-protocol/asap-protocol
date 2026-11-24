"""Pure mapping between an ASAP manifest and an A2A v1.0 Agent Card.

The functions here do no I/O and read no clock or global state. Each one
returns its result together with the source fields it dropped, as data (see
:mod:`asap.adapters.a2a.unmapped`). The field table is in
``docs/guides/interoperability.md`` ("Agent Card and manifest fields").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field, ValidationError

from asap.adapters.a2a._base import NonEmptyStr
from asap.adapters.a2a.card_security import (
    check_card_security_requirements,
    prepare_card_security_schemes,
)
from asap.adapters.a2a.errors import A2AMappingError, A2AMappingInputError
from asap.adapters.a2a.extension import AsapExtensionParams, SkillSchemas, build_asap_extension
from asap.adapters.a2a.models import AgentCard, AgentExtension, AgentInterface, AgentProvider
from asap.adapters.a2a.security import SecurityRequirement, SecurityScheme
from asap.adapters.a2a.unmapped import UNMAPPED_MANIFEST_FIELDS, DroppedField
from asap.models.base import ASAPBaseModel
from asap.models.entities import Manifest, Skill

_TagList = Annotated[list[NonEmptyStr], Field(min_length=1)]


class AgentCardInputs(ASAPBaseModel):
    """Card data that a manifest does not hold, for :func:`manifest_to_agent_card`.

    ``skill_tags`` needs an entry for every manifest skill: A2A requires a tag,
    and the mapping does not invent one. ``skill_names`` is optional; a skill
    without an entry is named by its ``id``. ``security_schemes`` and
    ``security_requirements`` describe the A2A interfaces, not the ASAP
    endpoint; see :mod:`asap.adapters.a2a.card_security` for their rules.

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
    security_schemes: dict[str, SecurityScheme] | None = None
    security_requirements: list[SecurityRequirement] | None = None


@dataclass(frozen=True, slots=True)
class CardMapping:
    """Result of :func:`manifest_to_agent_card`: the card and the manifest fields it dropped."""

    card: AgentCard
    dropped: tuple[DroppedField, ...]


def manifest_to_agent_card(manifest: Manifest, inputs: AgentCardInputs) -> CardMapping:
    """Build an A2A v1.0 Agent Card from an ASAP manifest.

    ASAP data that the card has no field for rides in the ASAP extension
    (:data:`asap.adapters.a2a.ASAP_EXTENSION_URI`). ``capabilities.streaming``
    is always written, so a card signature covers it. ``dropped`` lists each
    field of :data:`UNMAPPED_MANIFEST_FIELDS` that holds a value other than
    its default.

    Raises:
        A2AMappingInputError: A skill has no tags, an input names a skill that
            the manifest does not have, or a security input breaks a rule of
            :mod:`asap.adapters.a2a.card_security`.
        A2AMappingError: The card would not be valid, for example because a
            required field would be empty or the manifest has no skill.

    Example:
        >>> card = manifest_to_agent_card(manifest, inputs).card  # doctest: +SKIP
        >>> card.model_dump(mode="json")  # proto JSON form  # doctest: +SKIP
    """
    source = f"manifest {manifest.id!r}"
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    _require_unique_skill_ids(skill_ids, source)
    _require_known_skill_ids("skill_tags", inputs.skill_tags, skill_ids)
    _require_known_skill_ids("skill_names", inputs.skill_names, skill_ids)
    skills = [_card_skill(skill, inputs) for skill in manifest.capabilities.skills]
    try:
        extension = _asap_extension_for(manifest)
    except ValidationError as exc:
        raise _invalid_target_error(source, "set of ASAP extension params", exc) from exc
    try:
        card = AgentCard.model_validate(_card_fields(manifest, inputs, skills, extension))
    except ValidationError as exc:
        raise _invalid_target_error(source, "A2A v1.0 Agent Card", exc) from exc
    return CardMapping(card=card, dropped=_dropped_manifest_fields(manifest))


def _card_fields(
    manifest: Manifest,
    inputs: AgentCardInputs,
    skills: list[dict[str, Any]],
    extension: AgentExtension,
) -> dict[str, Any]:
    """The card as one by-name dict, so a validation error names its full path."""
    return {
        "name": manifest.name,
        "description": manifest.description,
        "supported_interfaces": inputs.interfaces,
        "provider": inputs.provider,
        "version": manifest.version,
        "documentation_url": inputs.documentation_url,
        "capabilities": {"streaming": manifest.capabilities.streaming, "extensions": [extension]},
        "security_schemes": prepare_card_security_schemes(inputs.security_schemes),
        "security_requirements": check_card_security_requirements(
            inputs.security_requirements, inputs.security_schemes
        ),
        "default_input_modes": inputs.default_input_modes,
        "default_output_modes": inputs.default_output_modes,
        "skills": skills,
        "icon_url": inputs.icon_url,
    }


def _dropped_manifest_fields(manifest: Manifest) -> tuple[DroppedField, ...]:
    dropped = []
    for field, reason in UNMAPPED_MANIFEST_FIELDS.items():
        owner, _, name = field.rpartition(".")
        model: BaseModel = manifest.capabilities if owner == "capabilities" else manifest
        default = type(model).model_fields[name].get_default(call_default_factory=True)
        if getattr(model, name) != default:
            dropped.append(DroppedField(path=field, field=field, reason=reason))
    return tuple(dropped)


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


def _card_skill(skill: Skill, inputs: AgentCardInputs) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": inputs.skill_names.get(skill.id, skill.id),
        "description": skill.description,
        "tags": _tags_for(skill.id, inputs),
    }


def _tags_for(skill_id: str, inputs: AgentCardInputs) -> list[str]:
    tags = inputs.skill_tags.get(skill_id)
    if tags is None:
        raise A2AMappingInputError(
            "skill_tags",
            f"skill {skill_id!r} has no entry in skill_tags: an A2A skill needs at least "
            "one tag, and the mapping does not invent one",
        )
    return tags


def _require_known_skill_ids(
    input_name: str, values: Mapping[str, object], skill_ids: list[str]
) -> None:
    unknown = sorted(set(values) - set(skill_ids))
    if unknown:
        raise A2AMappingInputError(
            input_name,
            f"{input_name} names skills that the source does not have: {unknown}; "
            f"expected only ids from {sorted(skill_ids)}",
        )


def _require_unique_skill_ids(skill_ids: list[str], source: str) -> None:
    duplicates = sorted({skill_id for skill_id in skill_ids if skill_ids.count(skill_id) > 1})
    if duplicates:
        raise A2AMappingError(
            f"{source} has duplicate skill ids {duplicates}: tags, names, and schemas "
            "are keyed by skill id, so each id must be unique",
            fields=["skills"],
        )


def _invalid_target_error(source: str, target: str, exc: ValidationError) -> A2AMappingError:
    """Describe which target fields failed, without echoing input values."""
    problems = exc.errors(include_input=False, include_url=False)
    fields = [".".join(str(part) for part in problem["loc"]) for problem in problems]
    details = "; ".join(f"{field}: {p['msg']}" for field, p in zip(fields, problems, strict=True))
    return A2AMappingError(
        f"{source} cannot map to a valid {target}: {details}", fields=fields or [target]
    )
