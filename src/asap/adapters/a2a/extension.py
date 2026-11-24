"""The ASAP extension: ASAP manifest data that an A2A Agent Card has no field for.

It rides in ``capabilities.extensions[]`` under :data:`ASAP_EXTENSION_URI`, so
a card signature covers it. The params keep the manifest's snake_case names.
A breaking change to the params needs a new URI (A2A §4.6.3). An added optional
param does not, because a reader ignores params it does not know.

Example params::

    {"agent_urn": "urn:asap:agent:research", "asap_endpoint": "https://a.example/asap",
     "asap_version": "0.1", "state_persistence": false,
     "skill_schemas": {"web_research": {"input_schema": {"type": "object"}}}}
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool

from asap.adapters.a2a._base import NonEmptyStr
from asap.adapters.a2a.models import AgentExtension

ASAP_EXTENSION_URI = "https://asap-protocol.com/a2a/extensions/manifest/v1"
"""URI of the ASAP extension (the project controls the domain)."""

_ASAP_EXTENSION_DESCRIPTION = "ASAP manifest data: agent URN, ASAP endpoint, and skill schemas"


class _ExtensionParamsModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class SkillSchemas(_ExtensionParamsModel):
    """The JSON Schemas of one ASAP skill, keyed by skill ``id`` in ``skill_schemas``."""

    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class AsapExtensionParams(_ExtensionParamsModel):
    """The ``params`` of the ASAP extension. Unknown params are ignored on read.

    ``skill_schemas`` lists only skills that have a schema.
    """

    agent_urn: NonEmptyStr
    asap_endpoint: NonEmptyStr
    events_endpoint: NonEmptyStr | None = None
    asap_version: NonEmptyStr
    state_persistence: StrictBool = False
    skill_schemas: dict[str, SkillSchemas] | None = None


def build_asap_extension(params: AsapExtensionParams) -> AgentExtension:
    """Return the card's extension entry for *params*, with ``required`` false.

    ``required`` stays false so that an A2A client that does not know ASAP can
    still use the card.
    """
    return AgentExtension(
        uri=ASAP_EXTENSION_URI,
        description=_ASAP_EXTENSION_DESCRIPTION,
        params=params.model_dump(mode="json", exclude_none=True),
    )
