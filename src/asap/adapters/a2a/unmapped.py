"""Source fields that the card ↔ manifest mapping does not carry, with the reason.

The interoperability guide and the compliance checks
read these tables. A mapping call reports each value it dropped as a
:class:`DroppedField` whose ``field`` is a key of the matching table.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DroppedField:
    """One source value that a mapping call dropped.

    ``path`` is the concrete source path (``skills[1].tags``), ``field`` is the
    row of the unmapped-field table (``skills[].tags``), and ``reason`` comes
    from that row.
    """

    path: str
    field: str
    reason: str


_NOT_CARRIED = "No A2A v1.0 field, and not in the ASAP extension params"

UNMAPPED_MANIFEST_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "supported_versions": (
            "ASAP HTTP wire versions of the ASAP endpoint. Each A2A interface has "
            "its own protocolVersion. Not in the ASAP extension params"
        ),
        "auth": (
            "Configures the ASAP endpoint. The card's security schemes describe "
            "the A2A interfaces and come from caller input"
        ),
        "signature": "Signs the manifest, not the card. Agent Card signatures are separate",
        "sla": _NOT_CARRIED,
        "verification": "A registry trust badge. A card must not assert it about itself",
        "ttl_seconds": "No A2A v1.0 field. A2A uses HTTP caching headers for the card",
        "capabilities.mcp_tools": _NOT_CARRIED,
        "capabilities.hardware": _NOT_CARRIED,
        "capabilities.inference": _NOT_CARRIED,
    }
)
"""Manifest fields that manifest → card drops, keyed by field path, with the reason."""

_NO_MANIFEST_FIELD = "No manifest field"
_A2A_SECURITY = (
    "Describes the A2A interfaces, not the ASAP endpoint. A card signature does not cover all of it"
)

UNMAPPED_CARD_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "supportedInterfaces": (
            "A2A endpoints. The manifest endpoint is the ASAP endpoint, from the ASAP "
            "extension or the caller"
        ),
        "provider": _NO_MANIFEST_FIELD,
        "documentationUrl": _NO_MANIFEST_FIELD,
        "iconUrl": _NO_MANIFEST_FIELD,
        "capabilities.pushNotifications": _NO_MANIFEST_FIELD,
        "capabilities.extendedAgentCard": _NO_MANIFEST_FIELD,
        "capabilities.extensions[]": "No manifest field for an extension other than ASAP's",
        "securitySchemes": _A2A_SECURITY,
        "securityRequirements": _A2A_SECURITY,
        "defaultInputModes": "No manifest field. ASAP skills use JSON Schema",
        "defaultOutputModes": "No manifest field. ASAP skills use JSON Schema",
        "skills[].name": (
            "No manifest field. Listed only when it differs from the skill id, which "
            "manifest → card uses as the name"
        ),
        "skills[].tags": _NO_MANIFEST_FIELD,
        "skills[].examples": _NO_MANIFEST_FIELD,
        "skills[].inputModes": "No manifest field. ASAP skills use JSON Schema",
        "skills[].outputModes": "No manifest field. ASAP skills use JSON Schema",
        "skills[].securityRequirements": _A2A_SECURITY,
        "signatures": (
            "Cover the card, not a manifest. Verify them before mapping; "
            "the manifest signature is a different scheme"
        ),
    }
)
"""Card fields (JSON names) that card → manifest drops, with the reason.

``[]`` stands for any list index; a mapping call reports the concrete path.
"""
