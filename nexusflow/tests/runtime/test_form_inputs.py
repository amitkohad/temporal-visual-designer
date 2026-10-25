"""Pure, bounded managed form input selection and immutable snapshot tests."""

import copy
import json

import pytest

from nexusflow.modules.durable_runtime.form_inputs import (
    FormInputError,
    resolve_form_inputs,
    validate_form_input_bindings,
)


def binding(path="/name", **changes):
    return {"$binding": {"schema_version": "0.1", "source": "input", "path": path, **changes}}


def fixture(target=None):
    target = target or {"type": "string"}
    config = {
        "rule": "any_one",
        "user_ids": ["alice"],
        "form": {
            "definition": {
                "form_id": "details",
                "schema_version": "0.1",
                "data_schema": {
                    "type": "object",
                    "properties": {"name": target, "later": {"type": "string"}},
                    "required": ["name", "later"],
                    "additionalProperties": False,
                },
            },
            "initial_values": {"name": binding()},
        },
    }
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    return config, schema


def test_validates_without_inventing_inputs_and_resolves_immutable_snapshot():
    config, schema = fixture()
    inputs = {"name": "Ada"}
    before = copy.deepcopy((config, schema, inputs))
    stripped = validate_form_input_bindings(config, schema)
    assert stripped["form"]["initial_values"] == {}
    resolved = resolve_form_inputs(config, schema, inputs)
    assert resolved["form"]["initial_values"] == {"name": "Ada"}
    assert (config, schema, inputs) == before
    # Missing required 'later' is intentional: the approver fills it in.
    assert "later" not in resolved["form"]["initial_values"]


@pytest.mark.parametrize(
    "config", [{"rule": "any_one", "user_ids": ["alice"]}, {"rule": "any_one", "form": None}]
)
def test_no_form_dictionary_bytes_unchanged(config):
    assert json.dumps(resolve_form_inputs(config, {}, {})) == json.dumps(config)


def test_literal_form_preserves_existing_unvalidated_initial_semantics():
    config, schema = fixture()
    config["form"]["initial_values"] = {"name": 123}
    assert json.dumps(resolve_form_inputs(config, schema, {})) == json.dumps(config)


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "step", "node_id": "producer"},
        {"node_id": "producer"},
        {"schema_version": "0.2"},
        {"extra": "secret-marker"},
        {"path": "name"},
        {"path": "/name~2"},
        {"path": "/" + "a" * 2048},
        {"path": "/missing"},
    ],
)
def test_rejects_invalid_binding_contract(changes):
    config, schema = fixture()
    config["form"]["initial_values"]["name"] = binding(**changes)
    with pytest.raises(FormInputError, match="^Invalid managed form input mapping$"):
        validate_form_input_bindings(config, schema)


@pytest.mark.parametrize(
    "value",
    [
        {"$binding": {"schema_version": "0.1", "source": "input", "path": "/name"}, "other": True},
        {"nested": binding()},
        [binding()],
        {"$binding": "secret-marker"},
    ],
)
def test_rejects_partial_and_nested_binding_envelopes(value):
    config, schema = fixture()
    config["form"]["initial_values"]["name"] = value
    with pytest.raises(FormInputError):
        validate_form_input_bindings(config, schema)


@pytest.mark.parametrize("location", ["user_ids", "extra", "definition", "unknown_initial"])
def test_rejects_bindings_outside_supported_slot(location):
    config, schema = fixture()
    if location == "definition":
        config["form"]["definition"]["layout"] = {"bad": binding()}
    elif location == "unknown_initial":
        config["form"]["initial_values"]["undeclared"] = binding()
    else:
        config[location] = [binding()]
    with pytest.raises(FormInputError):
        validate_form_input_bindings(config, schema)


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {"type": "object", "required": ["name"], "properties": {"name": {}}},
        {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": ["string", "null"]}},
        },
        {"type": "object", "required": ["name"], "properties": {"name": {"type": "integer"}}},
        {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"$ref": "https://secret-marker.invalid"}},
        },
    ],
)
def test_rejects_unproven_source_path_and_type(source):
    config, _ = fixture()
    with pytest.raises(FormInputError):
        validate_form_input_bindings(config, source)


@pytest.mark.parametrize("key,encoded", [("a/b", "a~1b"), ("a~b", "a~0b"), ("~1", "~01")])
def test_nested_required_objects_and_json_pointer_escapes(key, encoded):
    config, _ = fixture()
    config["form"]["initial_values"]["name"] = binding("/employee/" + encoded)
    schema = {
        "type": "object",
        "required": ["employee"],
        "properties": {
            "employee": {
                "type": "object",
                "required": [key],
                "properties": {key: {"type": "string"}},
            }
        },
    }
    assert (
        resolve_form_inputs(config, schema, {"employee": {key: "Ada"}})["form"]["initial_values"][
            "name"
        ]
        == "Ada"
    )


def test_array_index_paths_rejected():
    config, _ = fixture()
    config["form"]["initial_values"]["name"] = binding("/employees/0")
    schema = {
        "type": "object",
        "required": ["employees"],
        "properties": {"employees": {"type": "array", "items": {"type": "string"}}},
    }
    with pytest.raises(FormInputError):
        validate_form_input_bindings(config, schema)


def test_integer_source_to_number_target_and_readonly_initial_field():
    config, _ = fixture({"type": "number", "readOnly": True})
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "integer"}}}
    assert resolve_form_inputs(config, schema, {"name": 42})["form"]["initial_values"]["name"] == 42


@pytest.mark.parametrize(
    "target,value",
    [
        ({"type": "string", "minLength": 3}, "x"),
        ({"type": "string", "format": "email"}, "secret-marker"),
        ({"type": "string", "enum": ["approved"]}, "secret-marker"),
        ({"type": "string"}, None),
        ({"type": "string"}, 12),
    ],
)
def test_resolved_values_enforce_constraints_without_disclosure(target, value):
    config, schema = fixture(target)
    with pytest.raises(FormInputError) as error:
        resolve_form_inputs(config, schema, {"name": value})
    assert str(error.value) == "Invalid managed form input mapping"
    assert error.value.__cause__ is None


def test_missing_runtime_input_fails_without_disclosure():
    config, schema = fixture()
    with pytest.raises(FormInputError):
        resolve_form_inputs(config, schema, {})


def test_resolved_object_is_independent_and_cannot_contain_reserved_binding():
    config, _ = fixture({"type": "object"})
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "object"}}}
    inputs = {"name": {"safe": ["Ada"]}}
    result = resolve_form_inputs(config, schema, inputs)
    result["form"]["initial_values"]["name"]["safe"].append("changed")
    assert inputs == {"name": {"safe": ["Ada"]}}
    with pytest.raises(FormInputError):
        resolve_form_inputs(config, schema, {"name": {"nested": binding()}})


@pytest.mark.parametrize("kind", ["bytes", "depth", "nonfinite", "cycle"])
def test_resolved_snapshot_limits(kind):
    config, _ = fixture({"type": "object"})
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "object"}}}
    value = {}
    if kind == "bytes":
        value["text"] = "x" * 262144
    elif kind == "depth":
        current = value
        for _ in range(33):
            current["child"] = {}
            current = current["child"]
    elif kind == "nonfinite":
        value["number"] = float("nan")
    else:
        value["cycle"] = value
    with pytest.raises(FormInputError):
        resolve_form_inputs(config, schema, {"name": value})


def test_root_input_selection_into_object_field():
    config, schema = fixture({"type": "object"})
    config["form"]["initial_values"]["name"] = binding("")
    assert resolve_form_inputs(config, schema, {"name": "Ada"})["form"]["initial_values"][
        "name"
    ] == {"name": "Ada"}


@pytest.mark.parametrize("key", ["if", "allOf", "then", "$ref", "a/b", "a~b"])
def test_business_field_names_do_not_act_as_schema_keywords(key):
    config, _ = fixture()
    pointer = "/" + key.replace("~", "~0").replace("/", "~1")
    config["form"]["initial_values"]["name"] = binding(pointer)
    schema = {"type": "object", "required": [key], "properties": {key: {"type": "string"}}}
    assert (
        resolve_form_inputs(config, schema, {key: "Ada"})["form"]["initial_values"]["name"] == "Ada"
    )


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("default", {"if": {}, "$ref": "https://secret.invalid"}),
        ("examples", [{"allOf": [], "then": {}, "$dynamicRef": "https://secret.invalid"}]),
        ("const", {"if": "business-data"}),
        ("enum", [{"oneOf": "business-data"}]),
    ],
)
def test_annotation_and_literal_data_do_not_act_as_schemas(keyword, value):
    config, schema = fixture()
    schema["properties"]["name"][keyword] = value
    assert validate_form_input_bindings(config, schema)["form"]["initial_values"] == {}


@pytest.mark.parametrize(
    "keyword,child",
    [
        ("properties", {"other": {"$ref": "https://secret.invalid"}}),
        ("$defs", {"if": {"allOf": []}}),
        ("items", {"if": {}}),
        ("prefixItems", [{"oneOf": []}]),
        ("dependentSchemas", {"if": {"$dynamicRef": "https://secret.invalid"}}),
        ("additionalProperties", {"anyOf": []}),
        ("not", {"$ref": "https://secret.invalid"}),
    ],
)
def test_real_schema_positions_still_reject_references_and_compositions(keyword, child):
    config, schema = fixture()
    if keyword == "properties":
        schema[keyword].update(child)
    else:
        schema[keyword] = child
    with pytest.raises(FormInputError):
        validate_form_input_bindings(config, schema)
