"""Bounded, offline validation of the supported server form definition subset."""

import re

from pydantic import ValidationError

from nexusflow.contracts.models import DataSchema
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.designer_forms.form_types import ValidationRule, VisibilityRule
from nexusflow.modules.project_versioning.types import FormDefinition


def _part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _schema_issues(
    schema: DataSchema, path: str, *, field_annotations: bool = False
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    stack: list[tuple[object, str, int]] = [(schema, path, 0)]
    while stack:
        value, location, depth = stack.pop()
        if depth > 32:
            issues.append(
                ValidationIssue(
                    location, "schema_depth", "Schema nesting exceeds the supported limit"
                )
            )
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                target = f"{location}/{_part(str(key))}"
                property_names = (
                    location.endswith("/properties")
                    or location.endswith("/$defs")
                    or location.endswith("/definitions")
                )
                if not property_names and key in {"$ref", "$dynamicRef"}:
                    issues.append(
                        ValidationIssue(
                            target, "unsupported_reference", "Schema references are not supported"
                        )
                    )
                if not property_names and key in {"pattern", "patternProperties"}:
                    issues.append(
                        ValidationIssue(
                            target,
                            "unsupported_pattern",
                            "User-defined regular expressions are not supported",
                        )
                    )
                if not property_names and key in {"anyOf", "oneOf"}:
                    issues.append(
                        ValidationIssue(
                            target,
                            "unsupported_union",
                            "Alternative schema branches are not supported",
                        )
                    )
                if not property_names and key in {"readOnly", "writeOnly"}:
                    direct_field = (
                        field_annotations
                        and location.startswith(f"{path}/properties/")
                        and location.count("/") == path.count("/") + 2
                    )
                    if not direct_field or not isinstance(child, bool):
                        issues.append(
                            ValidationIssue(
                                target,
                                "unsupported_annotation",
                                "Only direct field boolean access annotations are supported",
                            )
                        )
                stack.append((child, target, depth + 1))
        elif isinstance(value, list):
            stack.extend(
                (child, f"{location}/{index}", depth + 1) for index, child in enumerate(value)
            )
    return issues


def validate_form_definition(form: FormDefinition) -> tuple[ValidationIssue, ...]:
    """Return located errors without echoing schema contents or executing user code."""
    issues: list[ValidationIssue] = []

    def issue(path: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(path, code, message))

    schema = form.data_schema
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        issue(
            "/data_schema", "form_schema", "Form schema must declare an object and its properties"
        )
        properties = {}
    if schema.get("additionalProperties") is not False:
        issue(
            "/data_schema/additionalProperties",
            "form_schema",
            "Form schema must forbid undeclared fields",
        )
    if len(properties) > 100:
        issue("/data_schema/properties", "field_limit", "Forms support at most 100 fields")
    for field_id, field_schema in properties.items():
        path = f"/data_schema/properties/{_part(field_id)}"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", field_id):
            issue(path, "field_id", "Field ID must be a stable identifier")
        if (
            not isinstance(field_schema, dict)
            or not isinstance(field_schema.get("type"), str)
            or field_schema.get("type")
            not in {
                "string",
                "number",
                "integer",
                "boolean",
                "object",
                "array",
                "null",
            }
        ):
            issue(path, "field_schema", "Field schema must declare one supported type")
    issues.extend(_schema_issues(schema, "/data_schema", field_annotations=True))
    for name, rules, model in (
        ("visibility_rules", form.visibility_rules, VisibilityRule),
        ("validation_rules", form.validation_rules, ValidationRule),
    ):
        if len(rules) > 100:
            issue(f"/{name}", "rule_limit", "Forms support at most 100 rules of each kind")
        seen: set[str] = set()
        for index, raw in enumerate(rules):
            path = f"/{name}/{index}"
            try:
                rule = model.model_validate(raw)
            except ValidationError:
                issue(path, "invalid_rule", "Invalid versioned form rule")
                continue
            if isinstance(rule, VisibilityRule):
                if rule.field_id not in properties:
                    issue(
                        f"{path}/field_id", "unknown_field", "Rule references an undeclared field"
                    )
                if rule.field_id in seen:
                    issue(
                        f"{path}/field_id",
                        "duplicate_rule",
                        "A field may have only one visibility rule",
                    )
                seen.add(rule.field_id)
                issues.extend(_schema_issues(rule.when, f"{path}/when"))
            else:
                issues.extend(_schema_issues(rule.data_schema, f"{path}/data_schema"))
    for field_id, permissions in form.field_permissions.items():
        path = f"/field_permissions/{_part(field_id)}"
        if field_id not in properties:
            issue(path, "unknown_field", "Permissions reference an undeclared field")
        if (
            not isinstance(permissions, dict)
            or set(permissions) != {"read_only"}
            or not isinstance(permissions.get("read_only"), bool)
        ):
            issue(
                path, "field_permissions", "Field permissions require only a boolean read_only flag"
            )
    if set(form.layout) - {"field_order"}:
        issue("/layout", "unsupported_layout", "Unsupported form layout attributes")
    if "field_order" in form.layout:
        order = form.layout["field_order"]
        if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
            issue("/layout/field_order", "field_order", "Field order must be a list of field IDs")
        else:
            seen_order: set[str] = set()
            for index, item in enumerate(order):
                assert isinstance(item, str)
                if item not in properties or item in seen_order:
                    issue(
                        f"/layout/field_order/{index}",
                        "field_order",
                        "Field order contains an unknown or repeated field",
                    )
                seen_order.add(item)
    if form.attachment_policy:
        issue(
            "/attachment_policy",
            "unsupported_attachments",
            "Attachment policies require a future attachment service",
        )
    return tuple(issues)
