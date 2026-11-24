"""Typed errors of the A2A adapter (:mod:`asap.adapters.a2a`).

These errors are raised locally, by parsing and mapping. They use the default
JSON-RPC code of :class:`asap.errors.ASAPError`; no new code is reserved.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from asap.errors import FatalError

if TYPE_CHECKING:
    from pydantic import ValidationError

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


class A2ACanonicalFormError(A2AAdapterError):
    """The card has no canonical form, so no signature can cover it.

    Raised for nesting deeper than the ``a2a-sdk`` limit and for text that is
    not valid Unicode (a lone surrogate): the SDK has no form for either one.
    """

    def __init__(self, message: str) -> None:
        super().__init__("asap:adapters/a2a/canonical_form", message, {})


class A2ACardNotSignableError(A2AAdapterError):
    """``sign_agent_card`` refused: the signature would not cover the whole card, or the key is wrong.

    ``paths`` lists the card values that the canonical form drops or rounds
    (empty for a key or algorithm problem). Messages never hold key material.
    """

    def __init__(self, message: str, *, paths: Sequence[str] = ()) -> None:
        super().__init__("asap:adapters/a2a/not_signable", message, {"paths": list(paths)})
        self.paths = tuple(paths)


InvalidSignatureReason = Literal[
    "malformed", "forbidden_algorithm", "key_mismatch", "bad_signature"
]
"""Why one signature fails the card; see :class:`A2AInvalidSignatureError`."""


class A2ACardVerificationError(A2AAdapterError):
    """The card's signatures do not meet the policy: the card must not be mapped or cached."""


class A2AInvalidSignatureError(A2ACardVerificationError):
    """One signature fails the card: it is malformed, uses ``none`` or HMAC, or does not verify.

    ``index`` is its position in ``signatures``; ``reason`` is stable. ``key_mismatch``
    means that the trusted key for its ``kid`` does not fit its ``alg``.
    """

    def __init__(self, index: int, reason: InvalidSignatureReason, detail: str) -> None:
        super().__init__(
            "asap:adapters/a2a/invalid_signature",
            f"signature {index} of the Agent Card is invalid ({reason}): {detail}",
            {"index": index, "reason": reason},
        )
        self.index = index
        self.reason: InvalidSignatureReason = reason


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

    @classmethod
    def from_validation_error(
        cls, source: str, target: str, exc: ValidationError
    ) -> A2AMappingError:
        """Name each target field that failed validation, without echoing input values."""
        problems = exc.errors(include_input=False, include_url=False)
        fields = [".".join(str(part) for part in problem["loc"]) for problem in problems]
        details = "; ".join(f"{f}: {p['msg']}" for f, p in zip(fields, problems, strict=True))
        return cls(f"{source} cannot map to a valid {target}: {details}", fields=fields or [target])
