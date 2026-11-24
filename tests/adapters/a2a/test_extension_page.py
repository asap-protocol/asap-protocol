"""The web page at the ASAP extension URI describes the params as the code defines them.

The page (``apps/web/src/app/a2a/extensions/manifest/v1``) renders its table and
example from ``_PAGE_DATA``. These tests fail when that file and
:class:`AsapExtensionParams` disagree, so the published description cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from asap.adapters.a2a import ASAP_EXTENSION_URI, AsapExtensionParams
from asap.adapters.a2a.extension import SkillSchemas, build_asap_extension

_PAGE_DATA = Path(__file__).resolve().parents[3] / "apps/web/src/lib/a2a/asap-extension-v1.json"


def _page_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_PAGE_DATA.read_text(encoding="utf-8"))
    return data


def _json_type(schema: dict[str, Any]) -> str:
    """Return the one JSON type of a property, ignoring the ``null`` of an optional field."""
    types = {alt["type"] for alt in schema.get("anyOf", [schema]) if alt["type"] != "null"}
    (json_type,) = types
    return json_type


def test_page_names_the_extension_uri() -> None:
    assert _page_data()["uri"] == ASAP_EXTENSION_URI


@pytest.mark.parametrize(
    ("model", "key"),
    [(AsapExtensionParams, "params"), (SkillSchemas, "skillSchemaFields")],
)
def test_page_rows_match_the_model(model: type[BaseModel], key: str) -> None:
    rows = _page_data()[key]
    properties = model.model_json_schema()["properties"]

    assert [row["name"] for row in rows] == list(model.model_fields)
    for row in rows:
        field = model.model_fields[row["name"]]
        assert row["required"] is field.is_required(), row["name"]
        assert row["type"] == _json_type(properties[row["name"]]), row["name"]
        expected_default = None if field.is_required() else field.default
        assert row.get("default") == expected_default, row["name"]


def test_page_example_is_what_asap_writes_in_a_card() -> None:
    example = _page_data()["example"]

    params = AsapExtensionParams.model_validate(example["params"])

    assert set(example["params"]) == set(AsapExtensionParams.model_fields)
    assert build_asap_extension(params).model_dump(mode="json") == example
