"""Bounded workflow-input selection for managed approval form initial snapshots."""

import json

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import JsonValue

from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.designer_forms.types import Binding
from nexusflow.modules.human_tasks.forms import prepare_task_form


class FormInputError(ValueError):
    """A sanitized form binding or snapshot failure."""


def _fail() -> FormInputError:
    return FormInputError("Invalid managed form input mapping")


def _copy(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # Round-tripping also rejects cycles, non-finite numbers and non-JSON values.
    result: dict[str, JsonValue] = json.loads(json.dumps(value, allow_nan=False))
    return result


def _reserved(value: JsonValue) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "$binding" in item:
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _parts(
    binding: Binding, schema: dict[str, JsonValue]
) -> tuple[list[str], dict[str, JsonValue]]:
    if binding.source != "input" or binding.node_id is not None:
        raise _fail()
    current = schema
    parts: list[str] = []
    if binding.path:
        for raw in binding.path.split("/")[1:]:
            if "~" in raw.replace("~0", "").replace("~1", ""):
                raise _fail()
            key = raw.replace("~1", "/").replace("~0", "~")
            properties, required = current.get("properties"), current.get("required")
            if (
                current.get("type") != "object"
                or not isinstance(properties, dict)
                or not isinstance(required, list)
                or key not in required
                or not isinstance(properties.get(key), dict)
            ):
                raise _fail()
            child = properties[key]
            assert isinstance(child, dict)
            current = child
            parts.append(key)
    return parts, current


def _safe_schema(schema: dict[str, JsonValue]) -> bool:
    # Walk schema positions only. Property names and annotation/instance values
    # may contain business keys such as "if" without changing schema meaning.
    pending = [schema]
    maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    singles = {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "items",
        "additionalItems",
        "unevaluatedItems",
        "contains",
        "not",
        "contentSchema",
    }
    forbidden = {"$ref", "$dynamicRef", "anyOf", "oneOf", "allOf", "if", "then", "else"}
    while pending:
        item = pending.pop()
        if forbidden & item.keys():
            return False
        for keyword, child in item.items():
            if keyword in maps and isinstance(child, dict):
                pending.extend(value for value in child.values() if isinstance(value, dict))
            elif keyword in singles and isinstance(child, dict):
                pending.append(child)
            elif keyword in {"prefixItems", "items"} and isinstance(child, list):
                pending.extend(value for value in child if isinstance(value, dict))
    return True


def _compatible(source: dict[str, JsonValue], target: dict[str, JsonValue]) -> bool:
    types = source.get("type")
    if isinstance(types, str):
        source_types = {types}
    elif isinstance(types, list) and types and all(isinstance(item, str) for item in types):
        source_types = {str(item) for item in types}
    else:
        return False
    target_type = target.get("type")
    accepted = {target_type} if isinstance(target_type, str) else set()
    if target_type == "number":
        accepted.add("integer")
    return source_types <= accepted


def _prepare(
    configuration: dict[str, JsonValue], input_schema: dict[str, JsonValue]
) -> tuple[dict[str, JsonValue], dict[str, tuple[list[str], dict[str, JsonValue]]]]:
    copied = _copy(configuration)
    mappings: dict[str, tuple[list[str], dict[str, JsonValue]]] = {}
    form = copied.get("form")
    if form is not None:
        if not isinstance(form, dict):
            raise _fail()
        definition, initial = form.get("definition"), form.get("initial_values", {})
        if not isinstance(definition, dict) or not isinstance(initial, dict):
            raise _fail()
        data_schema = definition.get("data_schema")
        properties = data_schema.get("properties") if isinstance(data_schema, dict) else None
        for field, value in list(initial.items()):
            if isinstance(value, dict) and "$binding" in value:
                if set(value) != {"$binding"} or not isinstance(properties, dict):
                    raise _fail()
                binding = Binding.model_validate(value["$binding"])
                target = properties.get(field)
                if not isinstance(target, dict) or not _safe_schema(input_schema):
                    raise _fail()
                parts, source = _parts(binding, input_schema)
                if not _compatible(source, target):
                    raise _fail()
                mappings[field] = (parts, target)
                del initial[field]
        prepare_task_form(
            json.dumps(definition, allow_nan=False), json.dumps(initial, allow_nan=False)
        )
    if _reserved(copied):
        raise _fail()
    return copied, mappings


def validate_form_input_bindings(
    configuration: dict[str, JsonValue], input_schema: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Validate selectors and return a copy with their initial-value fields removed."""
    try:
        return _prepare(configuration, input_schema)[0]
    except (ValueError, TypeError, RecursionError, OverflowError, FormError):
        raise _fail() from None


def resolve_form_inputs(
    configuration: dict[str, JsonValue],
    input_schema: dict[str, JsonValue],
    inputs: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Resolve against validated run inputs; never mutate any caller-owned object."""
    try:
        copied, mappings = _prepare(configuration, input_schema)
        if not mappings:
            return copied
        form = copied["form"]
        assert isinstance(form, dict)
        initial = form.setdefault("initial_values", {})
        assert isinstance(initial, dict)
        snapshot = _copy(inputs)
        for field, (parts, target) in mappings.items():
            value: JsonValue = snapshot
            for key in parts:
                if not isinstance(value, dict) or key not in value:
                    raise _fail()
                value = value[key]
            if _reserved(value) or not Draft202012Validator(
                target, format_checker=FormatChecker()
            ).is_valid(value):
                raise _fail()
            initial[field] = value
        prepare_task_form(
            json.dumps(form["definition"], allow_nan=False), json.dumps(initial, allow_nan=False)
        )
        return copied
    except (ValueError, TypeError, RecursionError, OverflowError, FormError):
        raise _fail() from None
