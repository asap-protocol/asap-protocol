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

from typing import Any, ClassVar

from pydantic import Field, StrictBool

from asap.adapters.a2a._base import A2AModel, NonEmptyStr


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
    """One skill of the agent (``AgentSkill``). ``tags`` needs at least one tag."""

    id: NonEmptyStr
    name: NonEmptyStr
    description: NonEmptyStr
    tags: list[NonEmptyStr] = Field(min_length=1)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None


class AgentCardSignature(A2AModel):
    """One JWS over the card, with a detached payload (``AgentCardSignature``).

    ``protected`` is the base64url-encoded protected header; ``header`` holds the
    optional unprotected header. This model never verifies them.
    """

    protected: NonEmptyStr
    signature: NonEmptyStr
    header: dict[str, Any] | None = None
