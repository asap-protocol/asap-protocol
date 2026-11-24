"""Pydantic models for the A2A v1.0 Agent Card.

Pinned source: ``specification/a2a.proto`` at tag ``v1.0.1``. The models follow
the proto, not the sample card in the spec prose:

- JSON names are camelCase (``protocolBinding``); attributes are snake_case
  (``protocol_binding``). Both spellings are accepted on read.
- Unknown fields are ignored on read; a dump never emits a field outside the proto.
- A field the proto marks ``REQUIRED`` must be present, and a required string or
  list must not be empty. ``streaming``, ``pushNotifications`` and
  ``extendedAgentCard`` are ``bool | None``: ``None`` is absent, ``False`` is kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import Field, StrictBool, model_validator

from asap.adapters.a2a._base import A2AModel, NonEmptyStr
from asap.adapters.a2a.errors import A2AUnsupportedVersionError
from asap.adapters.a2a.security import SecurityRequirement, SecurityScheme

# v0.3 had a top-level endpoint and version; v1.0 replaced both with supportedInterfaces.
_V03_MARKER_KEYS = ("url", "protocolVersion")
_V1_INTERFACE_KEYS = ("supportedInterfaces", "supported_interfaces")
_MAX_REPORTED_VERSION_LENGTH = 32


class AgentInterface(A2AModel):
    """One endpoint where the agent serves A2A (``AgentInterface``).

    ``protocol_binding`` is ``JSONRPC``, ``GRPC``, ``HTTP+JSON``, or a URI for a
    custom binding. ``protocol_version`` is ``Major.Minor``, for example ``"1.0"``.

    Example:
        >>> AgentInterface(url="https://a.example/a2a", protocol_binding="JSONRPC",
        ...                protocol_version="1.0").model_dump(mode="json")["protocolBinding"]
        'JSONRPC'
    """

    url: NonEmptyStr
    protocol_binding: NonEmptyStr
    protocol_version: NonEmptyStr
    tenant: str | None = None


class AgentProvider(A2AModel):
    """The organization that provides the agent (``AgentProvider``)."""

    url: NonEmptyStr
    organization: NonEmptyStr


class AgentExtension(A2AModel):
    """A protocol extension that the agent supports (``AgentExtension``).

    The proto marks no field required. ``required`` has implicit presence:
    ``False`` is the same as absent and is not dumped. ``params`` is free-form.
    """

    _omit_when_false: ClassVar[frozenset[str]] = frozenset({"required"})

    uri: str | None = None
    description: str | None = None
    required: StrictBool = False
    params: dict[str, Any] | None = None


class AgentCapabilities(A2AModel):
    """Optional A2A features of the agent (``AgentCapabilities``); all fields optional."""

    streaming: StrictBool | None = None
    push_notifications: StrictBool | None = None
    extensions: list[AgentExtension] | None = None
    extended_agent_card: StrictBool | None = None


class AgentSkill(A2AModel):
    """One skill of the agent (``AgentSkill``). ``tags`` needs at least one tag.

    ``security_requirements`` lists alternatives, like the card-level field.
    """

    id: NonEmptyStr
    name: NonEmptyStr
    description: NonEmptyStr
    tags: list[NonEmptyStr] = Field(min_length=1)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security_requirements: list[SecurityRequirement] | None = None


class AgentCardSignature(A2AModel):
    """One JWS over the card, with a detached payload (``AgentCardSignature``).

    ``protected`` is the base64url-encoded protected header; ``header`` holds the
    optional unprotected header. This model never verifies them.
    """

    protected: NonEmptyStr
    signature: NonEmptyStr
    header: dict[str, Any] | None = None


class AgentCard(A2AModel):
    """An A2A v1.0 Agent Card (``AgentCard``).

    Parsing a v0.3 card raises :class:`A2AUnsupportedVersionError`, from
    ``model_validate`` and ``model_validate_json`` alike. ``security_requirements``
    lists alternatives (see :mod:`asap.adapters.a2a.security`). ``signatures``
    are parsed here and verified elsewhere, never by this model.

    Example:
        >>> card = AgentCard.model_validate_json(raw_card_json)  # doctest: +SKIP
        >>> card.supported_interfaces[0].url  # doctest: +SKIP
        'https://agent.example.com/a2a'
    """

    name: NonEmptyStr
    description: NonEmptyStr
    supported_interfaces: list[AgentInterface] = Field(min_length=1)
    provider: AgentProvider | None = None
    version: NonEmptyStr
    documentation_url: str | None = None
    capabilities: AgentCapabilities
    security_schemes: dict[str, SecurityScheme] | None = None
    security_requirements: list[SecurityRequirement] | None = None
    default_input_modes: list[NonEmptyStr] = Field(min_length=1)
    default_output_modes: list[NonEmptyStr] = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    signatures: list[AgentCardSignature] | None = None
    icon_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_v03_card(cls, data: Any) -> Any:
        # Raised as is: pydantic wraps only ValueError and AssertionError.
        if not isinstance(data, Mapping) or any(key in data for key in _V1_INTERFACE_KEYS):
            return data
        if not any(key in data for key in _V03_MARKER_KEYS):
            return data
        found = str(data.get("protocolVersion") or "0.3")
        raise A2AUnsupportedVersionError(found[:_MAX_REPORTED_VERSION_LENGTH])
