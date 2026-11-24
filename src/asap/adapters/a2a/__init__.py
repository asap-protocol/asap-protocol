"""A2A v1.0 Agent Card models for the ASAP A2A bridge.

Pinned spec: A2A v1.0, ``specification/a2a.proto`` at tag ``v1.0.1``. This
package holds no A2A wire runtime and never imports ``a2a-sdk``.

Example:
    >>> from asap.adapters.a2a import AgentSkill
    >>> AgentSkill(id="search", name="Search", description="Web search", tags=["web"]).name
    'Search'
"""

from __future__ import annotations

from asap.adapters.a2a.errors import A2AAdapterError, A2AUnsupportedVersionError
from asap.adapters.a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

__all__ = [
    "A2AAdapterError",
    "A2AUnsupportedVersionError",
    "AgentCapabilities",
    "AgentCard",
    "AgentCardSignature",
    "AgentExtension",
    "AgentInterface",
    "AgentProvider",
    "AgentSkill",
]
