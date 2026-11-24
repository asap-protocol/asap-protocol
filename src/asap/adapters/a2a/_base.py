"""Shared base for the A2A v1.0 Agent Card models.

The rules here make ``model_dump(mode="json")`` give the proto JSON form that
``a2a-sdk`` produces (``MessageToDict``), which is the input of the signing
canonical form (``docs/guides/interoperability.md``, "Signed Agent Cards").
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer
from pydantic.alias_generators import to_camel, to_snake

NonEmptyStr = Annotated[str, Field(min_length=1)]
"""A string the proto marks ``REQUIRED``: an empty value counts as absent (AIP-203)."""


class A2AModel(BaseModel):
    """Base for A2A models: camelCase JSON, unknown fields ignored, frozen.

    A dump never writes ``null``: an unset optional field is omitted. Fields
    named in ``_omit_when_false`` are proto bools with implicit presence, where
    ``false`` is the same as absent, so a dump omits them when false.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="ignore",
        frozen=True,
    )

    _omit_when_false: ClassVar[frozenset[str]] = frozenset()

    @model_serializer(mode="wrap")
    def _omit_unset_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped: dict[str, Any] = handler(self)
        return {
            key: value
            for key, value in dumped.items()
            if value is not None and not (value is False and to_snake(key) in self._omit_when_false)
        }
