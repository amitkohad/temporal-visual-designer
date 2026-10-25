"""Activation requests, immutable state and destination readiness ports."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer

from nexusflow.contracts.models import Contract, DataSchema, Digest, Identifier, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.project_versioning.types import Release


class DeploymentEvidence(Contract):
    change_request_number: Annotated[str | None, Field(max_length=128)] = None
    notes: Annotated[str | None, Field(max_length=2000)] = None

    @field_validator("change_request_number", "notes")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Evidence must not be blank")
        return value


class ActivationRequest(Contract):
    project_id: Identifier
    release_id: Identifier
    configuration_revision: Identifier
    operation_id: Identifier
    expected_revision: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    runtime_version: Identifier = "proof.v1"
    action: Literal["activate", "rollback"] = "activate"
    evidence: DeploymentEvidence | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        # A return annotation replaces the model's serialization schema with a
        # generic mapping, hiding the explicit request fields from OpenAPI.
        result: dict[str, object] = handler(self)
        if self.evidence is None:
            result.pop("evidence", None)
        return result


class ActivationSummary(Contract):
    project_id: Identifier
    operation_id: Identifier
    release_id: Identifier
    revision: Annotated[int, Field(ge=1, lt=2**63)]
    previous_release_id: Identifier | None
    action: Literal["activate", "rollback"]
    runtime_version: Identifier
    configuration_revision: Identifier
    checksum: Digest
    actor_id: str
    created_at: str
    evidence: DeploymentEvidence | None = None


class ActivationHistory(Contract):
    project_id: Identifier
    items: tuple[ActivationSummary, ...]
    next_before_revision: int | None


class ActivationError(Exception):
    def __init__(self, code: str, message: str, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(message)


@dataclass(frozen=True)
class ActivationRecord:
    scope: Scope
    request: ActivationRequest
    release: Release
    revision: int
    previous_release_id: str | None
    actor_id: str
    created_at: str


@dataclass(frozen=True)
class ActivationEvent:
    operation_id: str
    action: str
    release_id: str
    checksum: str
    previous_release_id: str | None
    actor_id: str
    outcome: str
    created_at: str


class ConfigurationCompatibility(Protocol):
    def supports(self, scope: Scope, project_id: str, revision: str, schema: DataSchema) -> bool:
        """Require an immutable, valid destination configuration; never return secrets."""
        ...


class ActivationReadiness(Protocol):
    def validate(
        self, scope: Scope, release: Release, request: ActivationRequest
    ) -> tuple[ValidationIssue, ...]: ...

    def retain(self, scope: Scope, release: Release, usage_id: str) -> None:
        """Hold exact destination activity artifacts before activation commits."""
        ...
