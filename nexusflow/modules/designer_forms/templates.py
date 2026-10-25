"""Pure, tenant-neutral starting points for editable business process drafts."""

from copy import deepcopy
from typing import Literal

from pydantic import JsonValue, field_validator, model_validator

from nexusflow.contracts.models import (
    Contract,
    DataSchema,
    Edge,
    Identifier,
    NamedPorts,
    Node,
    StartDefinition,
    WorkflowDefinition,
    check_data_schema,
)
from nexusflow.modules.human_tasks.types import PrincipalIds
from nexusflow.modules.project_versioning.types import ProjectContent, VersioningError


class TemplateAssignment(Contract):
    rule: Literal["any_one", "all_approvers"] = "any_one"
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()

    @model_validator(mode="after")
    def valid_assignment(self) -> "TemplateAssignment":
        if not self.user_ids and not self.group_ids:
            raise ValueError("At least one user or group assignment is required")
        if len(set(self.user_ids)) != len(self.user_ids) or len(set(self.group_ids)) != len(
            self.group_ids
        ):
            raise ValueError("Assignment identifiers must be unique within each principal type")
        return self


class TemplateInfo(Contract):
    template_id: Identifier
    version: Literal["1"]
    name: str
    description: str
    workflow_id: Identifier
    assignment_slots: tuple[Identifier, ...]
    input_schema: DataSchema

    @field_validator("input_schema")
    @classmethod
    def valid_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


def list_templates() -> tuple[TemplateInfo, ...]:
    """Construct fresh metadata so callers cannot mutate a shared catalog."""
    definitions: tuple[tuple[str, str, str, tuple[str, ...], DataSchema], ...] = (
        (
            "employee-onboarding",
            "Employee onboarding",
            "Collect manager, HR and IT approvals for a new employee.",
            ("manager", "hr", "it"),
            {
                key: {"type": "string", "minLength": 1}
                for key in ("employee_name", "department", "start_date")
            },
        ),
        (
            "purchase-requisition",
            "Purchase requisition",
            "Collect manager and finance approvals for a purchase request.",
            ("manager", "finance"),
            {
                "requester": {"type": "string", "minLength": 1},
                "item": {"type": "string", "minLength": 1},
                "amount": {"type": "number", "minimum": 0.01},
                "currency": {"type": "string", "minLength": 3, "maxLength": 3},
            },
        ),
    )
    return tuple(
        TemplateInfo(
            template_id=template_id,
            version="1",
            name=name,
            description=description,
            workflow_id=template_id,
            assignment_slots=slots,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        )
        for template_id, name, description, slots, properties in definitions
    )


def get_template(template_id: str) -> TemplateInfo:
    for template in list_templates():
        if template.template_id == template_id:
            return template
    raise VersioningError("not_found", "Template not found")


def build_template(template_id: str, assignments: dict[str, TemplateAssignment]) -> ProjectContent:
    template = get_template(template_id)
    if set(assignments) != set(template.assignment_slots):
        raise VersioningError("invalid_content", "Template assignment slots do not match")
    nodes = [
        Node(
            node_id="start",
            kind="start",
            named_ports=NamedPorts(inputs=(), outputs=("out",)),
            input_mapping={},
        )
    ]
    properties = template.input_schema["properties"]
    assert isinstance(properties, dict)
    for slot in template.assignment_slots:
        context = deepcopy(properties)
        for schema in context.values():
            assert isinstance(schema, dict)
            schema["readOnly"] = True
        context["confirmed"] = {"type": "boolean", "const": True}
        context["notes"] = {"type": "string", "maxLength": 2000}
        configuration: dict[str, JsonValue] = assignments[slot].model_dump(mode="json")
        configuration["form"] = {
            "definition": {
                "form_id": f"{template_id}-{slot}",
                "schema_version": "0.1",
                "data_schema": {
                    "type": "object",
                    "properties": context,
                    "required": [*properties, "confirmed"],
                    "additionalProperties": False,
                },
            },
            "initial_values": {
                key: {"$binding": {"schema_version": "0.1", "source": "input", "path": f"/{key}"}}
                for key in properties
            },
        }
        nodes.append(
            Node(
                node_id=slot,
                kind="approval",
                named_ports=NamedPorts(inputs=("in",), outputs=("out",)),
                input_mapping={"human_task": configuration},
            )
        )
    nodes.append(
        Node(
            node_id="end",
            kind="end",
            named_ports=NamedPorts(inputs=("in",), outputs=()),
            input_mapping={},
        )
    )
    workflow = WorkflowDefinition(
        schema_version="0.1",
        workflow_id=template.workflow_id,
        input_schema=template.input_schema,
        output_schema={},
        start_definitions=(StartDefinition(start_id="manual", node_id="start", kind="manual"),),
        nodes=tuple(nodes),
        edges=tuple(
            Edge(
                source_node_id=source.node_id,
                source_port="out",
                target_node_id=target.node_id,
                target_port="in",
            )
            for source, target in zip(nodes, nodes[1:], strict=False)
        ),
        error_routes=(),
    )
    return ProjectContent(schema_version="0.1", workflows=(workflow,))
