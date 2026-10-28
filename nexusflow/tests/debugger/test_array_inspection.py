"""Explicit array inspection preserves privacy, positions and global budgets."""

import copy
import json

import pytest

from nexusflow.modules.designer_debugger.inspection import (
    MAX_ARRAY_ITEMS,
    MAX_RESPONSE_BYTES,
    inspect_inputs,
)
from nexusflow.tests.debugger.test_inspection import public, record, schema


def array(items, **extra):
    return {
        "type": "array",
        "x-nexusflow-inspect": True,
        "x-nexusflow-inspect-array": True,
        "items": items,
        **extra,
    }


def inspect(values, definition):
    return inspect_inputs(record({"items": values}), schema({"items": definition}))


def test_public_primitives_and_original_snapshot_unchanged():
    values = ["first", "second"]
    definition = array(public())
    frozen = record({"items": values})
    original = copy.deepcopy((frozen.request.input, frozen.plan.input_json, definition))
    result = inspect_inputs(frozen, schema({"items": definition}))
    assert result.values == {"items": values}
    assert result.redacted_paths == () and result.truncated is False
    assert (frozen.request.input, frozen.plan.input_json, definition) == original
    result.values["items"].append("response-only")
    assert frozen.request.input == {"items": ["first", "second"]}


@pytest.mark.parametrize("marker", [None, False, 1, "true"])
def test_new_array_marker_is_exact_and_old_schemas_stay_private(marker):
    definition = array(public())
    if marker is None:
        definition.pop("x-nexusflow-inspect-array")
    else:
        definition["x-nexusflow-inspect-array"] = marker
    result = inspect(["old-private-marker"], definition)
    assert result.values == {} and result.redacted_paths == ("/items",)
    assert "old-private-marker" not in result.model_dump_json()


def test_object_items_reveal_only_declared_public_children():
    definition = array(
        {
            "type": "object",
            "properties": {
                "sku": public(),
                "token": public(writeOnly=True),
                "notes": {"type": "string"},
            },
        }
    )
    result = inspect(
        [
            {
                "sku": "A",
                "token": "secret-marker",
                "notes": "unclassified",
                "dynamic-private-key": "hidden",
            }
        ],
        definition,
    )
    assert result.values == {"items": [{"sku": "A"}]}
    assert result.redacted_paths == ("/items/0", "/items/0/notes", "/items/0/token")
    encoded = result.model_dump_json()
    assert all(
        value not in encoded
        for value in ("secret-marker", "unclassified", "dynamic-private-key", "hidden")
    )


def test_nested_arrays_require_new_opt_in_at_every_layer():
    definition = array(array(public("integer")))
    assert inspect([[1, 2], [3]], definition).values == {"items": [[1, 2], [3]]}
    definition["items"].pop("x-nexusflow-inspect-array")
    result = inspect([[1, 2]], definition)
    assert result.values == {} and result.redacted_paths == ("/items",)


@pytest.mark.parametrize(
    "sensitive",
    [
        {"writeOnly": True},
        {"x-sensitive": True},
        {"x-secret": True},
        {"format": "password"},
        {"x-sensitive": "false"},
    ],
)
@pytest.mark.parametrize("position", ["container", "items"])
def test_parent_or_item_protection_hides_entire_array(sensitive, position):
    definition = array(public())
    definition.update(sensitive) if position == "container" else definition["items"].update(
        sensitive
    )
    result = inspect(["secret-marker", "another-secret"], definition)
    assert result.values == {} and result.redacted_paths == ("/items",)
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "items",
    [
        None,
        True,
        False,
        [],
        [public()],
        {},
        {"type": ["string", "null"], "x-nexusflow-inspect": True},
        {"type": "string"},
        {"type": "object"},
        array(public(), **{"x-nexusflow-inspect-array": False}),
    ],
)
def test_unsupported_or_unclassified_item_schema_hides_array(items):
    result = inspect(["hidden-marker"], array(items))
    assert result.values == {} and result.redacted_paths == ("/items",)
    assert result.truncated is False


@pytest.mark.parametrize(
    "keyword",
    [
        "prefixItems",
        "additionalItems",
        "contains",
        "minContains",
        "maxContains",
        "unevaluatedItems",
    ],
)
def test_tuple_and_containment_rules_never_reveal_values(keyword):
    result = inspect(["private-marker"], array(public(), **{keyword: {}}))
    assert result.values == {} and result.redacted_paths == ("/items",)


def test_missing_items_hides_even_empty_arrays():
    definition = array(public())
    definition.pop("items")
    assert inspect([], definition).values == {}
    assert inspect([], array(public())).values == {"items": []}


def test_redacted_items_keep_their_original_positions():
    result = inspect([1, "private-marker", 3], array(public("integer")))
    assert result.values == {"items": [1, None, 3]}
    assert result.redacted_paths == ("/items/1",)
    assert not result.truncated and "private-marker" not in result.model_dump_json()


def test_oversized_item_is_omitted_without_shifting_later_items():
    result = inspect(["first", "é" * 600, "last"], array(public()))
    assert result.values == {"items": ["first", None, "last"]}
    assert result.redacted_paths == ("/items/1",) and result.truncated


def test_null_items_remain_distinguishable_from_redacted_values():
    result = inspect([None, "hidden-marker", None], array(public("null")))
    assert result.values == {"items": [None, None, None]}
    assert result.redacted_paths == ("/items/1",)


def test_array_tail_is_bounded_per_container():
    result = inspect(list(range(MAX_ARRAY_ITEMS + 2)), array(public("integer")))
    assert result.values == {"items": list(range(MAX_ARRAY_ITEMS))}
    assert result.redacted_paths == ("/items",) and result.truncated


def test_nested_arrays_share_depth_budget():
    definition = public()
    value = "deep-private-marker"
    for _ in range(10):
        definition = array(definition)
        value = [value]
    result = inspect(value, definition)
    assert result.truncated
    assert "deep-private-marker" not in result.model_dump_json()


def test_large_item_objects_stop_at_global_visit_budget():
    properties = {f"field-{i}": public() for i in range(20)}
    value = [{key: "v" for key in properties} for _ in range(100)]
    result = inspect(value, array({"type": "object", "properties": properties}))
    assert result.truncated and len(result.values["items"]) < 100
    assert "/items" in result.redacted_paths


def test_array_aggregate_cannot_escape_complete_response_limit():
    result = inspect(["x" * 900 for _ in range(20)], array(public()))
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated
    assert len(result.model_dump_json().encode()) <= MAX_RESPONSE_BYTES


def test_indexed_redaction_paths_escape_declared_property_names():
    definition = schema(
        {"p/~": array({"type": "object", "properties": {"private/~": {"type": "string"}}})}
    )
    result = inspect_inputs(record({"p/~": [{"private/~": "hidden"}]}), definition)
    assert result.values == {"p/~": [{}]}
    assert result.redacted_paths == ("/p~1~0/0/private~1~0",)


def test_many_indexed_redactions_remain_bounded():
    definition = array(
        {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
    )
    result = inspect([{"a": "hidden-a", "b": "hidden-b"} for _ in range(100)], definition)
    assert result.truncated and result.redacted_paths == ("",)
    assert len(result.model_dump_json().encode()) <= MAX_RESPONSE_BYTES
    assert "hidden-" not in json.dumps(result.model_dump())
