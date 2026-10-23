"""Offline publication checks, not a form compiler or general secret detector.

Explicit resource IDs in node input mappings are checked; expressions remain
opaque until the authoring/compiler feature supplies its additional validator.
Credential checks recognize prohibited value keys, never arbitrary secret text.
JSON Schema declarations are intentionally excluded from instance-value scanning.
"""

from pydantic import JsonValue

from nexusflow.contracts.validation import ValidationIssue, validate_contract
from nexusflow.modules.project_versioning.types import ProjectContent

_PROHIBITED = frozenset(
    {
        "password",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "secretvalue",
        "connectionstring",
        "baseurl",
        "endpointurl",
        "environmentaddress",
    }
)


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scan(value: JsonValue, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{_escape(key)}"
            normalized = key.lower().replace("_", "").replace("-", "")
            if normalized in _PROHIBITED:
                issues.append(
                    ValidationIssue(
                        child_path,
                        "embedded_environment_value",
                        "Use an environment binding for this value",
                    )
                )
            else:
                issues.extend(_scan(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_scan(child, f"{path}/{index}"))
    return issues


class BuiltinContentValidator:
    def validate(self, content: ProjectContent) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not content.workflows:
            issues.append(
                ValidationIssue(
                    "/workflows", "missing_workflow", "A release requires at least one workflow"
                )
            )
        collections = (
            ("workflows", content.workflows, "workflow_id"),
            ("forms", content.forms, "form_id"),
            ("connections", content.connections, "connection_id"),
            ("tests", content.tests, "scenario_id"),
        )
        for name, items, attribute in collections:
            seen: set[str] = set()
            for index, item in enumerate(items):
                identifier = str(getattr(item, attribute))
                if identifier in seen:
                    issues.append(
                        ValidationIssue(
                            f"/{name}/{index}/{attribute}",
                            "duplicate_id",
                            "Resource IDs must be unique",
                        )
                    )
                seen.add(identifier)

        declared = {
            (ref.activity_id, ref.version, ref.artifact_digest) for ref in content.activity_refs
        }
        seen_refs: set[tuple[str, str, str]] = set()
        versions: dict[tuple[str, str], str] = {}
        for index, ref in enumerate(content.activity_refs):
            key = (ref.activity_id, ref.version, ref.artifact_digest)
            if key in seen_refs:
                issues.append(
                    ValidationIssue(
                        f"/activity_refs/{index}",
                        "duplicate_id",
                        "Activity references must be unique",
                    )
                )
            seen_refs.add(key)
            version_key = (ref.activity_id, ref.version)
            if version_key in versions and versions[version_key] != ref.artifact_digest:
                issues.append(
                    ValidationIssue(
                        f"/activity_refs/{index}",
                        "conflicting_activity",
                        "An activity version must have one digest",
                    )
                )
            versions[version_key] = ref.artifact_digest

        resources = {
            "form_id": {form.form_id for form in content.forms},
            "connection_id": {connection.connection_id for connection in content.connections},
            "workflow_id": {workflow.workflow_id for workflow in content.workflows},
        }
        for wi, workflow in enumerate(content.workflows):
            result = validate_contract("workflow", workflow.model_dump_json())
            issues.extend(
                ValidationIssue(f"/workflows/{wi}{issue.path}", issue.code, issue.message)
                for issue in result.issues
            )
            for ni, node in enumerate(workflow.nodes):
                path = f"/workflows/{wi}/nodes/{ni}"
                node_ref = node.activity_ref
                if (
                    node_ref
                    and (node_ref.activity_id, node_ref.version, node_ref.artifact_digest)
                    not in declared
                ):
                    issues.append(
                        ValidationIssue(
                            f"{path}/activity_ref",
                            "undeclared_activity",
                            "Activity must be declared by the project",
                        )
                    )
                for field, ids in resources.items():
                    if field in node.input_mapping:
                        value = node.input_mapping[field]
                        if not isinstance(value, str) or value not in ids:
                            issues.append(
                                ValidationIssue(
                                    f"{path}/input_mapping/{field}",
                                    "missing_resource",
                                    "Referenced resource is not in the project",
                                )
                            )
                issues.extend(_scan(node.input_mapping, f"{path}/input_mapping"))

        for index, test in enumerate(content.tests):
            if test.workflow_id not in resources["workflow_id"]:
                issues.append(
                    ValidationIssue(
                        f"/tests/{index}/workflow_id",
                        "missing_resource",
                        "Test workflow is not in the project",
                    )
                )
            issues.extend(_scan(test.input, f"/tests/{index}/input"))
        for index, form in enumerate(content.forms):
            for field in (
                "layout",
                "visibility_rules",
                "validation_rules",
                "field_permissions",
                "attachment_policy",
            ):
                issues.extend(_scan(form.model_dump(mode="json")[field], f"/forms/{index}/{field}"))
        issues.extend(_scan(content.layout, "/layout"))
        return tuple(issues)
