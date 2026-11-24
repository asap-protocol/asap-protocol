"""Typed errors of the A2A adapter (:mod:`asap.adapters.a2a`).

These errors are raised locally, by parsing and mapping. They use the default
JSON-RPC code of :class:`asap.errors.ASAPError`; no new code is reserved.
"""

from __future__ import annotations

from collections.abc import Sequence

from asap.errors import FatalError

A2A_SUPPORTED_VERSION = "1.0"
"""The only A2A version whose Agent Card the adapter reads (pinned proto tag ``v1.0.1``)."""


class A2AAdapterError(FatalError):
    """Base class for the errors of :mod:`asap.adapters.a2a`."""


class A2AUnsupportedVersionError(A2AAdapterError):
    """The document is an Agent Card of another A2A version, such as v0.3.

    Example:
        >>> raise A2AUnsupportedVersionError("0.3.0")
        Traceback (most recent call last):
        ...
        asap.adapters.a2a.errors.A2AUnsupportedVersionError: A2A Agent Card version ...
    """

    def __init__(self, found_version: str) -> None:
        super().__init__(
            "asap:adapters/a2a/unsupported_version",
            f"A2A Agent Card version {found_version!r} is not supported: expected an "
            f"A2A v{A2A_SUPPORTED_VERSION} card with 'supportedInterfaces' "
            "(a top-level 'url' or 'protocolVersion' marks a v0.3 card)",
            {"found_version": found_version, "supported_version": A2A_SUPPORTED_VERSION},
        )
        self.found_version = found_version


class A2AMappingInputError(A2AAdapterError):
    """A caller input that the mapping needs is missing, or it conflicts with the source.

    ``input_name`` names the input, for example ``"skill_tags"``.
    """

    def __init__(self, input_name: str, message: str) -> None:
        super().__init__("asap:adapters/a2a/mapping_input", message, {"input": input_name})
        self.input_name = input_name


class A2AMappingError(A2AAdapterError):
    """The source cannot map to a valid target, for example a card with an empty field.

    ``fields`` lists the target or source paths at fault, for example ``["description"]``.
    """

    def __init__(self, message: str, *, fields: Sequence[str]) -> None:
        super().__init__("asap:adapters/a2a/mapping", message, {"fields": list(fields)})
        self.fields = tuple(fields)
