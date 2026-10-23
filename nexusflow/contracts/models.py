"""Foundation v0.1 contracts; validation does not establish authentication."""

from datetime import datetime
from typing import Annotated, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Version = Literal["0.1"]
DataSchema = dict[str, JsonValue]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Scope(Contract):
    organization_id: Identifier
    environment_id: Identifier


class Actor(Contract):
    issuer: Annotated[str, Field(min_length=1, max_length=2048)]
    subject: Identifier
    directory_tenant_id: Identifier


class RequestContext(Contract):
    schema_version: Version
    request_id: Identifier
    correlation_id: Identifier
    scope: Scope
    actor: Actor
    resolved_permissions: tuple[Identifier, ...]


class ErrorEnvelope(Contract):
    schema_version: Version
    request_id: Identifier
    code: Literal[
        "unauthenticated",
        "forbidden",
        "not_found",
        "conflict",
        "invalid_input",
        "incompatible_dependency",
        "unavailable",
    ]
    message: Annotated[str, Field(min_length=1, max_length=1024)]


class ActivityRef(Contract):
    activity_id: Identifier
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    artifact_digest: Digest


class ExecutionPolicy(Contract):
    timeout_seconds: Annotated[int, Field(gt=0, le=86400)]
    maximum_attempts: Annotated[int, Field(ge=1, le=100)]


class ActivityDescriptor(Contract):
    descriptor_schema_version: Version
    activity_ref: ActivityRef
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    category: Identifier
    description: Annotated[str, Field(min_length=1, max_length=2048)]
    input_schema: DataSchema
    output_schema: DataSchema
    configuration_schema: DataSchema
    connection_requirements: tuple[Identifier, ...]
    execution_policy: ExecutionPolicy
    runtime_requirements: tuple[Identifier, ...]
    documentation: Annotated[str, Field(max_length=8192)]

    @field_validator("input_schema", "output_schema", "configuration_schema")
    @classmethod
    def valid_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


def check_data_schema(value: DataSchema) -> DataSchema:
    # Only inspect schema syntax here; never resolve remote references or execute code.
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError:
        raise ValueError("Invalid JSON Schema 2020-12") from None
    return value


class NamedPorts(Contract):
    inputs: tuple[Identifier, ...]
    outputs: tuple[Identifier, ...]


class Node(Contract):
    node_id: Identifier
    kind: Literal[
        "start",
        "end",
        "activity",
        "approval",
        "decision",
        "parallel",
        "join",
        "loop",
        "child_workflow",
        "wait",
        "set_data",
        "fail",
    ]
    named_ports: NamedPorts
    input_mapping: dict[str, JsonValue]
    activity_ref: ActivityRef | None = None
    policy: ExecutionPolicy | None = None
    maximum_iterations: Annotated[int, Field(gt=0, le=10000)] | None = None


class Edge(Contract):
    source_node_id: Identifier
    source_port: Identifier
    target_node_id: Identifier
    target_port: Identifier


class StartDefinition(Contract):
    start_id: Identifier
    node_id: Identifier
    kind: Literal["manual", "api", "form", "schedule", "webhook"]


class WorkflowDefinition(Contract):
    schema_version: Version
    workflow_id: Identifier
    input_schema: DataSchema
    output_schema: DataSchema
    start_definitions: Annotated[tuple[StartDefinition, ...], Field(min_length=1, max_length=100)]
    nodes: Annotated[tuple[Node, ...], Field(min_length=2, max_length=1000)]
    edges: Annotated[tuple[Edge, ...], Field(max_length=5000)]
    error_routes: Annotated[tuple[Edge, ...], Field(max_length=5000)]

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


class ProjectDefinition(Contract):
    schema_version: Version
    project_id: Identifier
    organization_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=128)]
    workflows: Annotated[tuple[WorkflowDefinition, ...], Field(min_length=1, max_length=100)]
    activity_refs: tuple[ActivityRef, ...]


class StepEvent(Contract):
    schema_version: Version
    event_id: Identifier
    scope: Scope
    run_id: Identifier
    sequence: Annotated[int, Field(ge=1)]
    node_id: Identifier
    invocation_id: Identifier
    branch_path: tuple[Identifier, ...]
    loop_iteration: Annotated[int, Field(ge=0)]
    attempt: Annotated[int, Field(ge=1)]
    kind: Literal["ready", "running", "waiting", "paused", "completed", "failed", "cancelled"]
    timestamp: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")]
    redacted_payload_refs: tuple[Identifier, ...]

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid UTC timestamp") from None
        return value


CONTRACTS: dict[str, type[Contract]] = {
    "request-context": RequestContext,
    "error": ErrorEnvelope,
    "activity-descriptor": ActivityDescriptor,
    "workflow": WorkflowDefinition,
    "project": ProjectDefinition,
    "step-event": StepEvent,
}
