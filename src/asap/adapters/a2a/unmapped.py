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
