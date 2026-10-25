"""Unbound methods are valid handlers only when the first parameter is self or cls.

``inspect.signature`` on a bound method or a ``classmethod`` drops the first
parameter, so those callables already look like ``(envelope, manifest)``.
Registering the function on the class keeps ``self``. A third parameter with
any other name is rejected.
"""

from __future__ import annotations

import pytest

from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.transport.handlers import validate_handler


class _AgentHandler:
    """Instance method registered from the class, before it is bound."""

    def handle(self, envelope: Envelope, manifest: Manifest) -> Envelope:
        return envelope


def test_validate_handler_accepts_unbound_instance_method() -> None:
    """``Class.handle`` is ``(self, envelope, manifest)`` and is accepted."""
    validate_handler(_AgentHandler.handle)


def test_validate_handler_accepts_function_whose_first_parameter_is_cls() -> None:
    """A function named ``cls`` matches the classmethod shape in the allow-list."""

    def handle(cls: object, envelope: Envelope, manifest: Manifest) -> Envelope:
        return envelope

    validate_handler(handle)


def test_validate_handler_rejects_three_parameters_without_self_or_cls() -> None:
    """Three parameters are not enough when the first name is neither self nor cls."""

    def handle(ctx: object, envelope: Envelope, manifest: Manifest) -> Envelope:
        return envelope

    with pytest.raises(TypeError, match="3 parameters"):
        validate_handler(handle)
