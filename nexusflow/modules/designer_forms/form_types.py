"""Server form ports and immutable render/validation snapshots."""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import field_validator

from nexusflow.contracts.models import Contract, DataSchema, Identifier, Scope, check_data_schema
from nexusflow.contracts.validation import ValidationIssue


class VisibilityRule(Contract):
    schema_version: Literal["0.1"]
    field_id: Identifier
    when: DataSchema

    @field_validator("when")
    @classmethod
    def valid_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


class ValidationRule(Contract):
    schema_version: Literal["0.1"]
    data_schema: DataSchema

    @field_validator("data_schema")
    @classmethod
    def valid_schema(cls, value: DataSchema) -> DataSchema:
        return check_data_schema(value)


@dataclass(frozen=True)
class AuthorizedForm:
    """Trusted adapter output, never a client-supplied request context."""

    definition_json: str
    current_values_json: str
    revision: int
    readable_fields: frozenset[str]
    editable_fields: frozenset[str]


@dataclass(frozen=True)
class FormField:
    field_id: str
    label: str
    schema_json: str
    editable: bool
    required: bool
    write_only: bool


@dataclass(frozen=True)
class FormView:
    revision: int
    fields: tuple[FormField, ...]
    values_json: str


class FormError(Exception):
    def __init__(self, code: str, message: str, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(message)


class FormGateway(Protocol):
    def authorize(
        self, scope: Scope, resource_id: str, action: Literal["preview", "submit"]
    ) -> AuthorizedForm:
        """Freshly authorize the scoped resource and load its server-owned snapshot."""
        ...

    def commit(
        self, scope: Scope, resource_id: str, expected_revision: int, values_json: str
    ) -> int:
        """Atomically recheck authority/revision and persist; raise on revoke/conflict."""
        ...
