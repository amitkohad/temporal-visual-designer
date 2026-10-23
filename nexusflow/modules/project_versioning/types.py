"""Feature-02 contracts; snapshots cross storage boundaries as immutable JSON."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue, field_validator

from nexusflow.contracts.models import (
    ActivityRef,
    Contract,
    DataSchema,
    Identifier,
    Scope,
    WorkflowDefinition,
    check_data_schema,
)
from nexusflow.contracts.validation import ValidationIssue


class FormDefinition(Contract):
    form_id: Identifier
    schema_version: Literal["0.1"]
    data_schema: DataSchema
    layout: dict[str, JsonValue] = Field(default_factory=dict)
    visibility_rules: tuple[dict[str, JsonValue], ...] = ()
    validation_rules: tuple[dict[str, JsonValue], ...] = ()
    field_permissions: dict[str, JsonValue] = Field(default_factory=dict)
    attachment_policy: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("data_schema")
    @classmethod
    def valid_data_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


class ConnectionDeclaration(Contract):
    connection_id: Identifier
    connector_type: Identifier
    configuration_schema: DataSchema

    @field_validator("configuration_schema")
    @classmethod
    def valid_data_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


class TestScenario(Contract):
    scenario_id: Identifier
    workflow_id: Identifier
    input: dict[str, JsonValue]


class ProjectContent(Contract):
    schema_version: Literal["0.1"]
    workflows: Annotated[tuple[WorkflowDefinition, ...], Field(max_length=100)] = ()
    forms: Annotated[tuple[FormDefinition, ...], Field(max_length=100)] = ()
    activity_refs: Annotated[tuple[ActivityRef, ...], Field(max_length=1000)] = ()
    connections: Annotated[tuple[ConnectionDeclaration, ...], Field(max_length=100)] = ()
    configuration_schema: DataSchema = Field(default_factory=dict)
    tests: Annotated[tuple[TestScenario, ...], Field(max_length=100)] = ()
    layout: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("configuration_schema")
    @classmethod
    def valid_data_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


class VersioningError(Exception):
    def __init__(self, code: str, message: str, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(message)


@dataclass(frozen=True)
class ProjectRecord:
    scope: Scope
    project_id: str
    name: str
    head_revision: int


@dataclass(frozen=True)
class Revision:
    scope: Scope
    project_id: str
    number: int
    parent_revision: int | None
    content_json: str
    summary: str
    author_id: str
    created_at: str


@dataclass(frozen=True)
class Release:
    scope: Scope
    project_id: str
    release_id: str
    source_revision: int
    content_json: str
    artifacts_json: str
    content_digest: str
    executable_digest: str
    author_id: str
    created_at: str


@dataclass(frozen=True)
class Change:
    path: str
    before_json: str | None
    after_json: str | None


@dataclass(frozen=True)
class ResolvedArtifact:
    reference: ActivityRef
    dependencies: tuple[ActivityRef, ...] = ()


class ProjectAuthorization(Protocol):
    def authorize(self, scope: Scope, permission: str, project_id: str) -> str:
        """Return the freshly authorized actor ID; deny through an exception."""
        ...


class ContentValidator(Protocol):
    def validate(self, content: ProjectContent) -> tuple[ValidationIssue, ...]: ...


class ArtifactResolver(Protocol):
    def resolve(self, reference: ActivityRef) -> ResolvedArtifact | None: ...


class ProjectRepository(Protocol):
    def create(
        self,
        scope: Scope,
        project_id: str,
        name: str,
        content_json: str,
        summary: str,
        author_id: str,
    ) -> Revision: ...
    def project(self, scope: Scope, project_id: str) -> ProjectRecord: ...
    def save(
        self,
        scope: Scope,
        project_id: str,
        expected_revision: int,
        content_json: str,
        summary: str,
        author_id: str,
    ) -> Revision: ...
    def revision(self, scope: Scope, project_id: str, number: int | None = None) -> Revision: ...
    def history(self, scope: Scope, project_id: str) -> tuple[Revision, ...]: ...
    def published(self, scope: Scope, project_id: str, idempotency_key: str) -> Release | None: ...
    def publish(
        self,
        scope: Scope,
        project_id: str,
        source_revision: int,
        idempotency_key: str,
        artifacts_json: str,
        content_digest: str,
        executable_digest: str,
        author_id: str,
    ) -> Release: ...
    def release(self, scope: Scope, project_id: str, release_id: str) -> Release: ...
    def retain(self, scope: Scope, project_id: str, release_id: str, reference_id: str) -> None: ...
    def release_reference(
        self, scope: Scope, project_id: str, release_id: str, reference_id: str
    ) -> None: ...
    def delete_release(self, scope: Scope, project_id: str, release_id: str) -> None: ...
