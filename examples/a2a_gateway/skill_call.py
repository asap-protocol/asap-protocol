"""The skill call decision: which skill runs, with which arguments, under which grant.

The skill selection rule and the A2A error mapping are in
``examples/a2a_gateway/README.md``. The gateway makes
this decision once, before the ``a2a-sdk`` dispatcher; the executor runs only
the decision it is handed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from a2a.types import Message
from google.protobuf.json_format import MessageToDict

from asap.adapters.a2a import ASAP_EXTENSION_URI
from asap.auth.capabilities import GrantCheckResult, check_capability_grant
from asap.auth.external_tokens import ExternalPrincipal

SKILL_SELECTION_KEY = ASAP_EXTENSION_URI
"""Message ``metadata`` key that selects a skill: ``{"skill_id": "<id>"}``."""


class SkillSelectionError(ValueError):
    """The message selects no skill the card lists, or its arguments are not one JSON object."""


@dataclass(frozen=True, slots=True)
class AuthorizedSkillCall:
    """A skill call that passed the grant check; the executor runs exactly this."""

    principal: ExternalPrincipal
    skill_id: str
    arguments: dict[str, Any]


def select_skill_call(message: Message, skill_ids: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """Return the selected skill id and its arguments, or raise :class:`SkillSelectionError`.

    A key that is present but malformed never falls back to the default skill.
    """
    metadata = MessageToDict(message.metadata)
    if SKILL_SELECTION_KEY in metadata:
        selection = metadata[SKILL_SELECTION_KEY]
        skill_id = selection.get("skill_id") if isinstance(selection, dict) else None
        if not isinstance(skill_id, str) or skill_id not in skill_ids:
            raise SkillSelectionError(
                f"metadata[{SKILL_SELECTION_KEY!r}] must be an object whose skill_id is one "
                f"of {sorted(skill_ids)}"
            )
    elif len(skill_ids) == 1:
        skill_id = skill_ids[0]
    else:
        raise SkillSelectionError(
            f"no skill selected: set metadata[{SKILL_SELECTION_KEY!r}] to "
            f'{{"skill_id": <one of {sorted(skill_ids)}>}}'
        )
    return skill_id, _arguments(message)


def _arguments(message: Message) -> dict[str, Any]:
    parts = list(message.parts)
    arguments = (
        MessageToDict(parts[0].data) if len(parts) == 1 and parts[0].HasField("data") else None
    )
    if not isinstance(arguments, dict):
        raise SkillSelectionError(
            "the message must have exactly one part: a data part holding a JSON object"
        )
    return arguments


def check_skill_grant(
    principal: ExternalPrincipal, skill_id: str, arguments: dict[str, Any]
) -> GrantCheckResult:
    """Check the principal's grant for the capability of *skill_id* (the skill id is the capability)."""
    grant = next((g for g in principal.grants if g.capability == skill_id), None)
    return check_capability_grant(grant, arguments)
