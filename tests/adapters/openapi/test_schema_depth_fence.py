"""OpenAPI $ref expansion stops after depth 50.

The cap is exclusive: a ref at depth 50 is still inlined, and a ref at
depth 51 is left untouched so a pathological spec cannot recurse further.
"""

from __future__ import annotations

from typing import Any

from asap.adapters.openapi.capability_mapper import _SchemaResolver

_ITEM_REF = "#/components/schemas/Item"


def _nest_ref(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"$ref": _ITEM_REF}
    for _ in range(depth):
        node = {"child": node}
    return node


def _node_at_depth(node: dict[str, Any], depth: int) -> dict[str, Any]:
    current = node
    for _ in range(depth):
        child = current["child"]
        assert isinstance(child, dict)
        current = child
    return current


def test_schema_ref_expands_at_depth_50_and_stops_at_depth_51() -> None:
    """Flipping the depth guard to >= would leave the depth-50 ref unresolved."""
    resolver = _SchemaResolver({"Item": {"type": "string", "title": "Item"}})

    at_limit = resolver.expand_refs(_nest_ref(50), frozenset())
    past_limit = resolver.expand_refs(_nest_ref(51), frozenset())

    assert _node_at_depth(at_limit, 50) == {"type": "string", "title": "Item"}
    assert _node_at_depth(past_limit, 51) == {"$ref": _ITEM_REF}
