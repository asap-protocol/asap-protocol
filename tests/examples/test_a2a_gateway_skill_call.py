"""The gateway's skill call decision.

The message ``metadata`` key named by the ASAP extension URI selects the
skill; a card with one skill makes it the default; a key that is present but
malformed never falls back. The arguments are the one data part, a JSON
object. The grant checked is the one for the selected skill's capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from a2a.helpers import new_data_part, new_text_part
from a2a.types import Message, Part, Role

from asap.adapters.a2a import ASAP_EXTENSION_URI
from asap.auth.capabilities import CapabilityGrant
from asap.auth.external_tokens import ExternalPrincipal
from examples.a2a_gateway.skill_call import (
    SKILL_SELECTION_KEY,
    SkillSelectionError,
    check_skill_grant,
    select_skill_call,
)

_SKILLS = ["echo", "greet"]


def _message(parts: list[Part], metadata: dict[str, Any] | None = None) -> Message:
    message = Message(role=Role.ROLE_USER, message_id="m-1", parts=parts)
    if metadata is not None:
        message.metadata.update(metadata)
    return message


def _selecting(value: Any, arguments: Any = None) -> Message:
    data = {"text": "hi"} if arguments is None else arguments
    return _message([new_data_part(data)], {SKILL_SELECTION_KEY: value})


def _principal(*grants: CapabilityGrant) -> ExternalPrincipal:
    expires = datetime(2030, 1, 1, tzinfo=timezone.utc)
    return ExternalPrincipal("https://idp.example", "user-1", None, (), expires, grants)


def test_the_selection_key_is_the_asap_extension_uri() -> None:
    assert SKILL_SELECTION_KEY == ASAP_EXTENSION_URI


def test_the_metadata_key_selects_the_skill_and_the_data_part_is_the_arguments() -> None:
    message = _selecting({"skill_id": "greet"}, {"name": "Ada", "count": 3})

    # A2A carries data as a protobuf Value: whole numbers arrive as floats.
    assert select_skill_call(message, _SKILLS) == ("greet", {"name": "Ada", "count": 3.0})


def test_without_the_key_the_only_skill_is_the_default() -> None:
    message = _message([new_data_part({"text": "hi"})])

    assert select_skill_call(message, ["echo"]) == ("echo", {"text": "hi"})


def test_without_the_key_and_with_several_skills_nothing_is_selected() -> None:
    with pytest.raises(SkillSelectionError, match="no skill selected"):
        select_skill_call(_message([new_data_part({"text": "hi"})]), _SKILLS)


@pytest.mark.parametrize(
    "value", ["echo", {"skill_id": 1}, {"skill": "echo"}, {"skill_id": "translate"}, None]
)
def test_a_malformed_selection_is_refused_even_with_a_default(value: Any) -> None:
    with pytest.raises(SkillSelectionError, match="skill_id is one of"):
        select_skill_call(_selecting(value), ["echo"])


@pytest.mark.parametrize(
    "parts",
    [
        [],
        [new_text_part("hi")],
        [new_data_part(["a", "list"])],
        [new_data_part({"a": 1}), new_data_part({"b": 2})],
    ],
)
def test_the_arguments_must_be_one_data_part_holding_an_object(parts: list[Part]) -> None:
    message = _message(parts, {SKILL_SELECTION_KEY: {"skill_id": "echo"}})

    with pytest.raises(SkillSelectionError, match="exactly one part"):
        select_skill_call(message, _SKILLS)


def test_the_grant_checked_is_the_one_for_the_selected_skill() -> None:
    constrained = CapabilityGrant(
        capability="greet", status="active", constraints={"language": {"in": ["en"]}}
    )
    principal = _principal(CapabilityGrant(capability="echo", status="active"), constrained)

    assert check_skill_grant(principal, "echo", {}).allowed
    assert check_skill_grant(principal, "greet", {"language": "en"}).allowed
    violated = check_skill_grant(principal, "greet", {"language": "fr"})
    assert [v.field for v in violated.violations] == ["language"]
    assert check_skill_grant(_principal(constrained), "echo", {}).grant is None
