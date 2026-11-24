"""Pure mapping between an ASAP manifest and an A2A v1.0 Agent Card.

The functions here do no I/O and read no clock or global state. Each one
returns its result together with the source fields it dropped, as data (see
:mod:`asap.adapters.a2a.unmapped`). The field table is in
``docs/guides/interoperability.md`` ("Agent Card and manifest fields").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field, StrictBool, ValidationError
from pydantic_core import PydanticSerializationError

from asap.adapters.a2a._base import NonEmptyStr
from asap.adapters.a2a.card_security import (
    check_card_security_requirements,
    prepare_card_security_schemes,
)
from asap.adapters.a2a.endpoint_checks import require_card_endpoint_shape
from asap.adapters.a2a.errors import (
    A2AMappingError,
    A2AMappingInputError,
    describe_card,
    describe_paths,
    quoted_prefix,
)
from asap.adapters.a2a.extension import (
    ASAP_EXTENSION_URI,
    AsapExtensionParams,
    SkillSchemas,
    build_asap_extension,
    read_asap_extension,
)
from asap.adapters.a2a.models import (
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from asap.adapters.a2a.security import SecurityRequirement, SecurityScheme
from asap.adapters.a2a.unmapped import (
    UNMAPPED_CARD_FIELDS,
    UNMAPPED_MANIFEST_FIELDS,
    DroppedField,
)
from asap.models.base import ASAPBaseModel
from asap.models.entities import Endpoint, Manifest, Skill

_TagList = Annotated[list[NonEmptyStr], Field(min_length=1)]


class AgentCardInputs(ASAPBaseModel):
    """Card data that a manifest does not hold, for :func:`manifest_to_agent_card`.

    ``skill_tags`` needs an entry for every manifest skill: A2A requires a tag,
    and the mapping does not invent one. ``skill_names`` is optional; a skill
    without an entry is named by its ``id``. ``security_schemes`` and
    ``security_requirements`` describe the A2A interfaces, not the ASAP
    endpoint; ``skill_security_requirements`` (skill ``id`` → requirements)
    holds a skill's own requirements under the same rules; see
    :mod:`asap.adapters.a2a.card_security`.
    Set ``a2a_streaming`` if the A2A interfaces differ from the ASAP endpoint.
    By default, the card uses the manifest's streaming value.

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
    skill_security_requirements: dict[str, list[SecurityRequirement]] = Field(default_factory=dict)
    a2a_streaming: StrictBool | None = None


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
    _require_known_skill_ids(
        "skill_security_requirements", inputs.skill_security_requirements, skill_ids
    )
    skills = [_card_skill(skill, inputs) for skill in manifest.capabilities.skills]
    a2a_streaming = inputs.a2a_streaming
    if a2a_streaming is None:
        a2a_streaming = manifest.capabilities.streaming
    try:
        extension = _asap_extension_for(manifest, a2a_streaming=a2a_streaming)
    except ValidationError as exc:
        raise A2AMappingError.from_validation_error(
            source, "set of ASAP extension params", exc
        ) from exc
    try:
        card = AgentCard.model_validate(
            _card_fields(manifest, inputs, skills, extension, a2a_streaming=a2a_streaming)
        )
    except ValidationError as exc:
        raise A2AMappingError.from_validation_error(source, "A2A v1.0 Agent Card", exc) from exc
    return CardMapping(card=card, dropped=_dropped_manifest_fields(manifest))


@dataclass(frozen=True, slots=True)
class ManifestMapping:
    """Result of :func:`agent_card_to_manifest`: the manifest and the card fields it dropped."""

    manifest: Manifest
    dropped: tuple[DroppedField, ...]


def agent_card_to_manifest(
    card: AgentCard, *, agent_id: str | None = None, endpoints: Endpoint | None = None
) -> ManifestMapping:
    """Build an ASAP manifest from an A2A v1.0 Agent Card.

    The URN, the endpoints, the ASAP version, state persistence, and the skill
    schemas come from the card's ASAP extension. If the extension has no
    ``asap_streaming`` value, the manifest uses the card's streaming value.
    A card without the extension needs
    ``agent_id`` and ``endpoints`` from the caller. When the card has it, a
    value that the caller also passes must equal the card's. The manifest is
    built from the parsed model, so a field outside the A2A v1.0 schema never
    reaches it, and no value that :func:`asap.adapters.a2a.canonical_card_form`
    reports as uncovered outside the ASAP extension params is mapped.

    This function checks no signature. Map the ``card`` of a
    :class:`asap.adapters.a2a.CardVerification` whose ``verified`` is true:
    :func:`asap.adapters.a2a.verify_agent_card` refuses a card whose ASAP
    extension params hold a value that no signature covers, so then every
    mapped value is covered. :class:`asap.adapters.a2a.AgentCardIngestor`
    does both steps.

    The card's own endpoints must be ``https`` (``wss`` for events) URLs with
    a host and no user info; the host itself is not checked here (see
    :mod:`asap.adapters.a2a.endpoint_checks`). Endpoints that the caller
    passes are the caller's own and are not checked. ``dropped`` lists each
    card value of :data:`UNMAPPED_CARD_FIELDS` that the manifest does not carry.

    Raises:
        A2AMappingInputError: A caller input is missing or conflicts with the card.
        A2AMappingError: The manifest would not be valid, for example because the
            card version is not one a manifest accepts; or the ASAP extension is
            broken or names an endpoint of the wrong shape; or two skills share
            an id; or a free-form member nests deeper than the model serializer
            allows.

    Example:
        >>> agent_card_to_manifest(card, agent_id="urn:asap:agent:x",
        ...     endpoints=Endpoint(asap="https://x.example/asap")).manifest  # doctest: +SKIP
    """
    source = describe_card(card.name)
    params = read_asap_extension(card)
    manifest_id, manifest_endpoints = _identity_for(params, agent_id, endpoints)
    _require_unique_skill_ids([skill.id for skill in card.skills], source)
    try:
        manifest = Manifest.model_validate(
            {
                "id": manifest_id,
                "name": card.name,
                "version": card.version,
                "description": card.description,
                "capabilities": _manifest_capabilities(card, params),
                "endpoints": manifest_endpoints,
            }
        )
    except ValidationError as exc:
        raise A2AMappingError.from_validation_error(source, "ASAP manifest", exc) from exc
    return ManifestMapping(manifest=manifest, dropped=_dropped_card_fields(card))


def _dropped_card_fields(card: AgentCard) -> tuple[DroppedField, ...]:
    try:
        card_json = card.model_dump(mode="json")
    except PydanticSerializationError as exc:
        # json.loads accepts far deeper nesting than the serializer, and the
        # canonical form does not cover a signature's unprotected header.
        raise A2AMappingError(
            f"{describe_card(card.name)} nests deeper than the model serializer allows in "
            "a free-form member (extension params or a signature's unprotected header)",
            fields=["capabilities.extensions[].params", "signatures[].header"],
        ) from exc
    carried = _carried_card_paths(card)
    return tuple(
        DroppedField(path=path, field=field, reason=reason)
        for field, reason in UNMAPPED_CARD_FIELDS.items()
        for path in _present_paths(card_json, field)
        if path not in carried
    )


def _carried_card_paths(card: AgentCard) -> set[str]:
    """Paths that match an unmapped row but that the mapping carries after all."""
    extensions = card.capabilities.extensions or []
    carried = {
        f"capabilities.extensions[{index}]"
        for index, extension in enumerate(extensions)
        if extension.uri == ASAP_EXTENSION_URI
    }
    carried |= {
        f"skills[{index}].name" for index, skill in enumerate(card.skills) if skill.name == skill.id
    }
    return carried


def _present_paths(document: Mapping[str, Any], field: str) -> list[str]:
    """Concrete paths in *document* for a table row such as ``skills[].tags``."""
    head, _, rest = field.partition(".")
    if head.endswith("[]"):
        name = head.removesuffix("[]")
        items = document.get(name) or []
        suffix = f".{rest}" if rest else ""
        return [f"{name}[{i}]{suffix}" for i, item in enumerate(items) if not rest or rest in item]
    if head not in document:
        return []
    if not rest:
        return [head]
    return [f"{head}.{path}" for path in _present_paths(document[head], rest)]


def _identity_for(
    params: AsapExtensionParams | None, agent_id: str | None, endpoints: Endpoint | None
) -> tuple[str, Endpoint]:
    if params is None:
        if agent_id is None or endpoints is None:
            missing = "agent_id" if agent_id is None else "endpoints"
            raise A2AMappingInputError(
                missing,
                f"the card has no ASAP extension ({ASAP_EXTENSION_URI}), so the caller "
                f"must pass agent_id and endpoints; {missing} is missing",
            )
        return agent_id, endpoints
    require_card_endpoint_shape("asap", params.asap_endpoint)
    if params.events_endpoint is not None:
        require_card_endpoint_shape("events", params.events_endpoint)
    card_endpoints = Endpoint(asap=params.asap_endpoint, events=params.events_endpoint)
    _require_equal("agent_id", agent_id, params.agent_urn)
    _require_equal("endpoints", endpoints, card_endpoints)
    return params.agent_urn, card_endpoints


def _require_equal(input_name: str, given: object, from_card: object) -> None:
    if given is not None and given != from_card:
        raise A2AMappingInputError(
            input_name,
            f"{input_name} {quoted_prefix(str(given))} conflicts with the card's ASAP "
            f"extension, which gives {quoted_prefix(str(from_card))}",
        )


def _manifest_capabilities(card: AgentCard, params: AsapExtensionParams | None) -> dict[str, Any]:
    schemas = (params.skill_schemas if params else None) or {}
    capabilities: dict[str, Any] = {
        "streaming": card.capabilities.streaming is True,
        "skills": [_manifest_skill(skill, schemas.get(skill.id)) for skill in card.skills],
    }
    if params is not None:
        capabilities["asap_version"] = params.asap_version
        capabilities["state_persistence"] = params.state_persistence
        if params.asap_streaming is not None:
            capabilities["streaming"] = params.asap_streaming
    return capabilities


def _manifest_skill(skill: AgentSkill, schemas: SkillSchemas | None) -> dict[str, Any]:
    return {
        "id": skill.id,
        "description": skill.description,
        "input_schema": deepcopy(schemas.input_schema) if schemas else None,
        "output_schema": deepcopy(schemas.output_schema) if schemas else None,
    }


def _card_fields(
    manifest: Manifest,
    inputs: AgentCardInputs,
    skills: list[dict[str, Any]],
    extension: AgentExtension,
    *,
    a2a_streaming: bool,
) -> dict[str, Any]:
    """The card as one by-name dict, so a validation error names its full path."""
    return {
        "name": manifest.name,
        "description": manifest.description,
        "supported_interfaces": inputs.interfaces,
        "provider": inputs.provider,
        "version": manifest.version,
        "documentation_url": inputs.documentation_url,
        "capabilities": {"streaming": a2a_streaming, "extensions": [extension]},
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


def _asap_extension_for(manifest: Manifest, *, a2a_streaming: bool) -> AgentExtension:
    schemas = {
        skill.id: SkillSchemas(input_schema=skill.input_schema, output_schema=skill.output_schema)
        for skill in manifest.capabilities.skills
        if skill.input_schema is not None or skill.output_schema is not None
    }
    asap_streaming = manifest.capabilities.streaming
    params = AsapExtensionParams(
        agent_urn=manifest.id,
        asap_endpoint=manifest.endpoints.asap,
        events_endpoint=manifest.endpoints.events,
        asap_version=manifest.capabilities.asap_version,
        state_persistence=manifest.capabilities.state_persistence,
        skill_schemas=schemas or None,
        asap_streaming=asap_streaming if asap_streaming != a2a_streaming else None,
    )
    return build_asap_extension(params)


def _card_skill(skill: Skill, inputs: AgentCardInputs) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": inputs.skill_names.get(skill.id, skill.id),
        "description": skill.description,
        "tags": _tags_for(skill.id, inputs),
        "security_requirements": check_card_security_requirements(
            inputs.skill_security_requirements.get(skill.id),
            inputs.security_schemes,
            input_name="skill_security_requirements",
            where=f"skill_security_requirements[{skill.id!r}]",
        ),
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
    # One counting pass: list.count per id was quadratic.
    duplicates = sorted(skill_id for skill_id, seen in Counter(skill_ids).items() if seen > 1)
    if duplicates:
        raise A2AMappingError(
            f"{source} has duplicate skill ids {describe_paths(duplicates)}: tags, names, and schemas "
            "are keyed by skill id, so each id must be unique",
            fields=["skills"],
        )
