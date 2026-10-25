"""Versioned browser-facing payloads; scope and placement never come from bodies."""

import json
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from nexusflow.contracts.models import Contract, Digest, Identifier


class JsonRequest(Contract):
    @model_validator(mode="after")
    def finite_json(self) -> "JsonRequest":
        try:
            json.dumps(self.model_dump(mode="json"), allow_nan=False)
        except (ValueError, RecursionError):
            raise ValueError("Request must contain finite JSON values") from None
        return self


class CreateProject(JsonRequest):
    project_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=200)]
    content: dict[str, JsonValue]


class ReassignTask(JsonRequest):
    command_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    user_ids: Annotated[list[Identifier], Field(max_length=1000)] = Field(default_factory=list)
    group_ids: Annotated[list[Identifier], Field(max_length=1000)] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonempty_assignment(self) -> "ReassignTask":
        if not self.user_ids and not self.group_ids:
            raise ValueError("At least one assignment is required")
        return self


class SaveProject(JsonRequest):
    expected_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    content: dict[str, JsonValue]


class ValidateProject(JsonRequest):
    content: dict[str, JsonValue]


class PublishProject(Contract):
    source_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    idempotency_key: Identifier


class StartWorkflow(JsonRequest):
    workflow_id: Identifier
    idempotency_key: Identifier
    input: dict[str, JsonValue] = Field(default_factory=dict)
    source: Literal["manual", "api"] = "manual"


class ProjectRevision(Contract):
    project_id: Identifier
    revision: int
    parent_revision: int | None
    summary: str
    author_id: str
    created_at: str
    content: dict[str, JsonValue]


class ProjectRelease(Contract):
    project_id: Identifier
    release_id: Identifier
    source_revision: int
    checksum: Digest


class ProjectActivation(Contract):
    revision: int
    release_id: Identifier | None
    project_id: Identifier | None = None
    operation_id: Identifier | None = None
    previous_release_id: Identifier | None = None
    runtime_version: Identifier | None = None
    configuration_revision: Identifier | None = None
    checksum: Digest | None = None
    actor_id: str | None = None
    created_at: str | None = None


class ExecutionAccepted(Contract):
    execution_id: Identifier
    release_id: Identifier
    configuration_revision: Identifier
    state: Literal["accepted"] = "accepted"


class SubmitTaskForm(JsonRequest):
    expected_revision: Annotated[int, Field(ge=1)]
    values: dict[str, JsonValue]


class TaskFormSubmission(Contract):
    revision: Annotated[int, Field(ge=1)]


class RestoreProject(Contract):
    source_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    expected_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]
    summary: Annotated[str, Field(min_length=1, max_length=1000)]


class ProjectRevisionMetadata(Contract):
    revision: int
    parent_revision: int | None
    summary: str
    author_id: str
    created_at: str


class ProjectHistory(Contract):
    project_id: Identifier
    revisions: tuple[ProjectRevisionMetadata, ...]
    next_before_revision: int | None


class ProjectChange(Contract):
    path: str
    before_json: str | None
    after_json: str | None


class ProjectComparison(Contract):
    project_id: Identifier
    before_revision: int
    after_revision: int
    include_layout: bool
    changes: tuple[ProjectChange, ...]
