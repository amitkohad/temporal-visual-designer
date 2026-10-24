"""Activation requests, immutable state and destination readiness ports."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field

from nexusflow.contracts.models import Contract, DataSchema, Identifier, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.project_versioning.types import Release


class ActivationRequest(Contract):
    project_id: Identifier
    release_id: Identifier
    configuration_revision: Identifier
    operation_id: Identifier
    expected_revision: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    runtime_version: Identifier = "proof.v1"
    action: Literal["activate", "rollback"] = "activate"


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
