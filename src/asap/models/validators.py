"""Shared validators for ASAP protocol models."""

import re

from asap.models.constants import AGENT_URN_PATTERN, MAX_URN_LENGTH

_AGENT_URN_RE = re.compile(AGENT_URN_PATTERN)

# Percentage format: optional minus, digits/dots, optional whitespace, optional %
# Matches parse_percentage in asap.economics.sla
_PERCENTAGE_RE = re.compile(r"^(-?[\d.]+)\s*%?\s*$", re.IGNORECASE)


def validate_percentage_format(v: str) -> str:
    """Validate percentage string format (e.g. '99.5%', '1%').

    Raises ValueError if string does not match expected pattern.
    """
    v = v.strip()
    if not _PERCENTAGE_RE.match(v):
        raise ValueError(f"Invalid percentage string: {v!r}")
    return v


def validate_agent_urn(v: str) -> str:
    """Validate agent URN format and length (pattern + max length)."""
    if len(v) > MAX_URN_LENGTH:
        raise ValueError(f"Agent URN must be at most {MAX_URN_LENGTH} characters, got {len(v)}")
    # fullmatch: the pattern's ``$`` alone also matches before a final newline.
    if not _AGENT_URN_RE.fullmatch(v):
        raise ValueError(f"Agent ID must follow URN format 'urn:asap:agent:{{name}}', got: {v}")
    return v
