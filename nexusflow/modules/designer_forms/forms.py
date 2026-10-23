"""Authoritative, offline form preview and partial submission validation.

The gateway owns fresh authorization, persisted values and transactional commits.
This library never accepts caller-provided field permissions or executes labels.
"""

import json
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from nexusflow.contracts.models import Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.designer_forms.form_definition import validate_form_definition
from nexusflow.modules.designer_forms.form_types import (
    AuthorizedForm,
    FormError,
    FormField,
    FormGateway,
    FormView,
    ValidationRule,
    VisibilityRule,
)
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import FormDefinition


def _parse_values(payload: str) -> dict[str, Any]:
    def invalid_constant(value: str) -> None:
        raise ValueError

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if len(payload.encode("utf-8")) > 262144:
            raise ValueError
        data = json.loads(payload, parse_constant=invalid_constant, object_pairs_hook=unique_pairs)
        if not isinstance(data, dict):
            raise ValueError
        canonical(data).encode("utf-8")
        pending = [(data, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 32:
                raise ValueError
            if isinstance(item, dict):
                pending.extend((value, depth + 1) for value in item.values())
            elif isinstance(item, list):
                pending.extend((value, depth + 1) for value in item)
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise FormError("invalid_input", "Form values must be a bounded JSON object") from None


def _validator(schema: Any) -> Draft202012Validator:
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _load(snapshot: AuthorizedForm) -> tuple[FormDefinition, dict[str, Any]]:
    try:
        if len(snapshot.definition_json.encode("utf-8")) > 1048576:
            raise ValueError
        definition = FormDefinition.model_validate_json(snapshot.definition_json)
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        raise FormError("invalid_definition", "Invalid form definition") from None
    issues = validate_form_definition(definition)
    if issues:
        raise FormError("invalid_definition", "Form definition is not supported", issues)
    try:
        current = _parse_values(snapshot.current_values_json)
    except FormError:
        raise FormError("invalid_definition", "Invalid stored form values") from None
    return definition, current


def _properties(definition: FormDefinition) -> dict[str, dict[str, Any]]:
    # The definition validator establishes the supported object/field shapes.
    return cast(dict[str, dict[str, Any]], definition.data_schema["properties"])


def _public_values(
    snapshot: AuthorizedForm, properties: dict[str, dict[str, Any]], values: dict[str, Any]
) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key in snapshot.readable_fields
        and key in properties
        and not properties[key].get("writeOnly", False)
    }


def _visible(definition: FormDefinition, public_values: dict[str, Any]) -> set[str]:
    visible = set(_properties(definition))
    for raw in definition.visibility_rules:
        rule = VisibilityRule.model_validate(raw)
        if not _validator(rule.when).is_valid(public_values):
            visible.discard(rule.field_id)
    return visible


def _editable(
    definition: FormDefinition, snapshot: AuthorizedForm, properties: dict[str, dict[str, Any]]
) -> set[str]:
    return {
        key
        for key in snapshot.editable_fields & snapshot.readable_fields
        if key in properties
        and not properties[key].get("readOnly", False)
        and not definition.field_permissions.get(key, {}).get("read_only", False)  # type: ignore[union-attr]
    }


def _schemas(definition: FormDefinition) -> list[dict[str, Any]]:
    return [
        dict(definition.data_schema),
        *(
            dict(ValidationRule.model_validate(raw).data_schema)
            for raw in definition.validation_rules
        ),
    ]


def _required(schema: dict[str, Any], values: dict[str, Any]) -> set[str]:
    required = set(schema.get("required", []))
    for child in schema.get("allOf", []):
        if isinstance(child, dict):
            required.update(_required(child, values))
    if "if" in schema:
        branch = schema.get("then" if _validator(schema["if"]).is_valid(values) else "else", {})
        if isinstance(branch, dict):
            required.update(_required(branch, values))
    for key, fields in schema.get("dependentRequired", {}).items():
        if key in values:
            required.update(fields)
    for key, child in schema.get("dependentSchemas", {}).items():
        if key in values and isinstance(child, dict):
            required.update(_required(child, values))
    return required


class FormService:
    def __init__(self, gateway: FormGateway) -> None:
        self.gateway = gateway

    def preview(self, scope: Scope, resource_id: str) -> FormView:
        snapshot = self.gateway.authorize(scope, resource_id, "preview")
        definition, current = _load(snapshot)
        properties = _properties(definition)
        public = _public_values(snapshot, properties, current)
        visible = _visible(definition, public) & snapshot.readable_fields
        editable = _editable(definition, snapshot, properties)
        required = set().union(*(_required(schema, public) for schema in _schemas(definition)))
        order = list(cast(list[str], definition.layout.get("field_order", [])))
        order.extend(key for key in sorted(properties) if key not in order)
        fields: list[FormField] = []
        for key in order:
            if key not in visible:
                continue
            schema = properties[key]
            secret = bool(schema.get("writeOnly", False))
            # Secret defaults/enums/examples must not be copied into preview metadata.
            metadata = {"type": schema.get("type"), "writeOnly": True} if secret else schema
            fields.append(
                FormField(
                    key,
                    str(schema.get("title", key)),
                    canonical(metadata),
                    key in editable,
                    key in required,
                    secret,
                )
            )
        return FormView(
            snapshot.revision,
            tuple(fields),
            canonical({key: value for key, value in public.items() if key in visible}),
        )

    def submit(
        self, scope: Scope, resource_id: str, expected_revision: int, values_json: str
    ) -> int:
        snapshot = self.gateway.authorize(scope, resource_id, "submit")
        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or expected_revision > 2**63 - 1
        ):
            raise FormError("invalid_input", "Invalid form revision")
        if expected_revision != snapshot.revision:
            raise FormError("conflict", "Form values changed; reload before submitting")
        definition, current = _load(snapshot)
        patch = _parse_values(values_json)
        properties = _properties(definition)
        editable = _editable(definition, snapshot, properties)
        if set(patch) - editable:
            raise FormError("forbidden", "Submission contains fields that cannot be edited")
        merged = {**current, **patch}
        public = _public_values(snapshot, properties, merged)
        if set(patch) - _visible(definition, public):
            raise FormError("forbidden", "Submission contains fields that cannot be edited")
        issues: list[ValidationIssue] = []
        for schema in _schemas(definition):
            for error in _validator(schema).iter_errors(merged):
                parts = list(error.absolute_path)
                if error.validator == "required" and isinstance(error.instance, dict):
                    missing = [key for key in error.validator_value if key not in error.instance]
                    if missing:
                        parts.append(missing[0])
                if parts and (
                    parts[0] not in snapshot.readable_fields
                    or properties.get(str(parts[0]), {}).get("writeOnly", False)
                ):
                    parts = []
                path = "".join(
                    "/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts
                )
                issues.append(
                    ValidationIssue(
                        path, "invalid_form_value", "Form values do not satisfy the business rules"
                    )
                )
                if len(issues) >= 100:
                    break
            if len(issues) >= 100:
                break
        if issues:
            raise FormError(
                "validation_failed", "Correct the form values before submitting", tuple(issues)
            )
        return self.gateway.commit(scope, resource_id, expected_revision, canonical(merged))
