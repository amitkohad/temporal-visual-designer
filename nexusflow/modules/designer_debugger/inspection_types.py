"""Bounded, redacted workflow-input inspection response."""

from typing import Annotated

from pydantic import Field, JsonValue

from nexusflow.contracts.models import Contract, Digest, Identifier


class DebugInputInspection(Contract):
    session_id: Identifier
    workflow_id: Identifier
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    definition_digest: Digest
    values: dict[str, JsonValue]
    redacted_paths: tuple[str, ...]
    truncated: bool
