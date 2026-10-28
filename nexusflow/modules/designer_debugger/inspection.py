"""Pure fail-closed input inspection; never resolve schemas or runtime resources."""

import json
import math
from typing import Any

from pydantic import JsonValue

from nexusflow.contracts.models import DataSchema
from nexusflow.modules.designer_debugger.inspection_types import DebugInputInspection
from nexusflow.modules.designer_debugger.types import DebugError, DebugRecord
from nexusflow.modules.project_versioning.canonical import canonical

MAX_RESPONSE_BYTES = 16384
MAX_SCALAR_BYTES = 1024
MAX_DEPTH = 8
MAX_VISITED = 1000
MAX_REDACTED_PATHS = 100
MAX_ARRAY_ITEMS = 100
_UNSUPPORTED = frozenset(
    {
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependencies",
        "patternProperties",
        "propertyNames",
        "unevaluatedProperties",
        "prefixItems",
        "additionalItems",
        "contains",
        "minContains",
        "maxContains",
        "unevaluatedItems",
    }
)
_OMIT = object()


def _pointer(parent: str, name: str) -> str:
    return parent + "/" + name.replace("~", "~0").replace("/", "~1")


def _safe_schema(schema: Any) -> bool:
    if type(schema) is not dict or any(key in schema for key in _UNSUPPORTED):
        return False
    if any(schema.get(key, False) is not False for key in ("writeOnly", "x-sensitive", "x-secret")):
        return False
    return "format" not in schema or (
        type(schema["format"]) is str and schema["format"].lower() != "password"
    )


def _supported_items(schema: Any) -> bool:
    if not _safe_schema(schema):
        return False
    kind = schema.get("type")
    if kind == "object":
        return type(schema.get("properties")) is dict
    if kind == "array":
        return (
            schema.get("x-nexusflow-inspect") is True
            and schema.get("x-nexusflow-inspect-array") is True
            and type(schema.get("items")) is dict
        )
    return kind in ("string", "number", "integer", "boolean", "null") and (
        schema.get("x-nexusflow-inspect") is True
    )


def inspect_inputs(record: DebugRecord, input_schema: DataSchema) -> DebugInputInspection:
    """Expose explicitly public primitives and homogeneous arrays of frozen input.

    Schema declarations are not evaluated and cannot perform network I/O. Dynamic
    keys and unsupported schema shapes are deliberately not inspected.
    """
    try:
        if len(record.plan.input_json.encode("utf-8")) > 65536:
            raise ValueError
        source = json.loads(record.plan.input_json)
        if type(source) is not dict or canonical(source) != canonical(record.request.input):
            raise ValueError
        return _inspect(record, source, input_schema)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise DebugError("unavailable", "Frozen debug inputs are unavailable") from None


def _inspect(
    record: DebugRecord, source: dict[str, JsonValue], input_schema: DataSchema
) -> DebugInputInspection:
    paths: set[str] = set()
    truncated = False
    visited = 0
    path_overflow = False

    def redact(path: str, *, limited: bool = False) -> object:
        nonlocal truncated, path_overflow
        truncated = truncated or limited
        if not path_overflow:
            paths.add(path)
            if len(paths) > MAX_REDACTED_PATHS:
                paths.clear()
                paths.add("")
                path_overflow = True
                truncated = True
        return _OMIT

    def visit(value: JsonValue, schema: Any, path: str, depth: int) -> Any:
        nonlocal visited, truncated
        visited += 1
        if visited > MAX_VISITED or depth > MAX_DEPTH:
            return redact(path, limited=True)
        if not _safe_schema(schema):
            return redact(path)
        kind = schema.get("type")
        if kind == "object" and type(value) is dict:
            properties = schema.get("properties")
            if type(properties) is not dict:
                return redact(path)
            result: dict[str, JsonValue] = {}
            for name in sorted(value):
                if visited >= MAX_VISITED:
                    redact(path, limited=True)
                    break
                if name not in properties:
                    visited += 1
                    redact(path)
                    continue
                child = visit(value[name], properties[name], _pointer(path, name), depth + 1)
                if child is not _OMIT:
                    result[name] = child
            return result
        if kind == "array" and type(value) is list:
            items = schema.get("items")
            if (
                schema.get("x-nexusflow-inspect") is not True
                or schema.get("x-nexusflow-inspect-array") is not True
                or not _supported_items(items)
            ):
                return redact(path)
            sequence: list[JsonValue] = []
            for index, item in enumerate(value):
                if index >= MAX_ARRAY_ITEMS or visited >= MAX_VISITED:
                    redact(path, limited=True)
                    break
                child = visit(item, items, _pointer(path, str(index)), depth + 1)
                # Item positions carry meaning; hidden values must never shift
                # subsequent public entries to different indices.
                sequence.append(None if child is _OMIT else child)
            return sequence
        if schema.get("x-nexusflow-inspect") is not True:
            return redact(path)
        matches = (
            (kind == "string" and type(value) is str)
            or (kind == "boolean" and type(value) is bool)
            or (kind == "integer" and type(value) is int)
            or (kind == "number" and type(value) in (int, float))
            or (kind == "null" and value is None)
        )
        if not matches or (type(value) is float and not math.isfinite(value)):
            return redact(path)
        if len(canonical(value).encode("utf-8")) > MAX_SCALAR_BYTES:
            return redact(path, limited=True)
        return value

    inspected = visit(source, input_schema, "", 0)
    values = inspected if type(inspected) is dict else {}

    def response() -> DebugInputInspection:
        return DebugInputInspection(
            session_id=record.session.session_id,
            workflow_id=record.session.workflow_id,
            source_revision=record.session.source_revision,
            definition_digest=record.session.definition_digest,
            values=values,
            redacted_paths=tuple(sorted(paths)),
            truncated=truncated,
        )

    result = response()
    if len(result.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
        # An oversized declared name or aggregate public payload must not escape
        # the complete response budget through values or diagnostic paths.
        values = {}
        paths = {""}
        truncated = True
        result = response()
    return result
