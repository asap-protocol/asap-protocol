"""Unit tests for OpenAPI adapter approval strength (OA-008)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from asap.adapters.openapi.approval import (
    collect_webauthn_required_capability_names,
    normalize_approval_strength_map,
    resolve_approval_strength,
)
from asap.adapters.openapi.capability_mapper import (
    OpenAPIExecutionKind,
    OpenAPICapability,
    map_openapi_to_capabilities,
)
from asap.adapters.openapi.spec_loader import load_spec
from asap.models.entities import Skill

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "openapi"


def _write_tmp(data: dict[str, Any], name: str) -> Path:
    path = _FIXTURES / f"_tmp_{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_normalize_approval_strength_map_uppercases_verbs() -> None:
    m = normalize_approval_strength_map({"get": "session", "POST": "webauthn"})
    assert m == {"GET": "session", "POST": "webauthn"}


def test_normalize_approval_strength_map_preserves_operation_id_case() -> None:
    m = normalize_approval_strength_map({"deletePet": "webauthn"})
    assert m == {"deletePet": "webauthn"}


def test_normalize_approval_strength_map_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="Invalid approval_strength"):
        normalize_approval_strength_map({"GET": "none"})


def test_normalize_approval_strength_map_rejects_empty_string_value() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_approval_strength_map({"GET": "   "})


def test_normalize_approval_strength_map_skips_whitespace_only_keys() -> None:
    out = normalize_approval_strength_map({"  \t": "session", "post": "webauthn"})
    assert out == {"POST": "webauthn"}


@pytest.mark.asyncio
async def test_collect_webauthn_uses_http_method_mapping() -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                },
            },
            "/items/{id}": {
                "delete": {
                    "operationId": "removeItem",
                    "responses": {"204": {"description": "ok"}},
                },
            },
        },
    }
    path = _write_tmp(raw, "approval_methods")
    try:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        names = collect_webauthn_required_capability_names(
            caps,
            {"GET": "session", "DELETE": "webauthn"},
        )
        assert "removeItem" in names
        assert "listItems" not in names
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_operation_id_overrides_method_strength() -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items/{id}": {
                "delete": {
                    "operationId": "softDelete",
                    "responses": {"204": {"description": "ok"}},
                },
            },
        },
    }
    path = _write_tmp(raw, "approval_op_override")
    try:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        mapping = normalize_approval_strength_map(
            {"DELETE": "webauthn", "softDelete": "session"},
        )
        assert (
            resolve_approval_strength(
                http_method="delete",
                operation_id=caps[0].operation_id,
                mapping=mapping,
            )
            == "session"
        )
        names = collect_webauthn_required_capability_names(
            caps,
            {"DELETE": "webauthn", "softDelete": "session"},
        )
        assert names == []
    finally:
        path.unlink(missing_ok=True)


def test_normalize_approval_strength_map_rejects_non_string_value() -> None:
    """A numeric strength is rejected before it can be treated as text."""
    raw: dict[str, Any] = {"DELETE": 1}
    with pytest.raises(ValueError, match="non-empty string"):
        normalize_approval_strength_map(raw)


def test_resolve_approval_strength_unmapped_verb_is_none() -> None:
    """A verb absent from the map is not implied to be session or webauthn."""
    strength = resolve_approval_strength(
        http_method="PATCH",
        operation_id=None,
        mapping={"GET": "session", "DELETE": "webauthn"},
    )
    assert strength is None


def test_resolve_approval_strength_strips_verb_and_ignores_unknown_operation_id() -> None:
    """Padded methods match the verb key; an unknown operationId does not block that match."""
    mapping = normalize_approval_strength_map({"DELETE": "webauthn", "POST": "session"})
    assert (
        resolve_approval_strength(
            http_method=" delete ",
            operation_id="DeletePet",
            mapping=mapping,
        )
        == "webauthn"
    )
    assert (
        resolve_approval_strength(
            http_method="\tpost\n",
            operation_id=None,
            mapping=mapping,
        )
        == "session"
    )


def test_collect_webauthn_skips_operations_without_a_webauthn_rule() -> None:
    """An empty approval map does not mark a capability as WebAuthn-required.

    A spec that omits ``operationId`` is stored as ``None``. That missing id
    is not itself a WebAuthn rule.
    """
    caps = [
        OpenAPICapability(
            skill=Skill(id="capA", description="delete a record"),
            http_method="delete",
            path_template="/a",
            execution_kind=OpenAPIExecutionKind.SYNC,
            operation_id="delA",
        ),
        OpenAPICapability(
            skill=Skill(id="capMissingOp", description="delete without an operation id"),
            http_method="delete",
            path_template="/missing",
            execution_kind=OpenAPIExecutionKind.SYNC,
            operation_id=None,
        ),
    ]
    assert collect_webauthn_required_capability_names(caps, {}) == []


def test_collect_webauthn_deduplicates_same_skill_id() -> None:
    skill = Skill(id="sharedId", description="x")
    caps = [
        OpenAPICapability(
            skill=skill,
            http_method="delete",
            path_template="/a",
            execution_kind=OpenAPIExecutionKind.SYNC,
            operation_id="delA",
        ),
        OpenAPICapability(
            skill=skill,
            http_method="delete",
            path_template="/b",
            execution_kind=OpenAPIExecutionKind.SYNC,
            operation_id="delB",
        ),
    ]
    names = collect_webauthn_required_capability_names(caps, {"DELETE": "webauthn"})
    assert names == ["sharedId"]
