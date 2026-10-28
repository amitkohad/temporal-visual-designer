"""Bounded, schema-redacted completed activity output response."""

from typing import Annotated

from pydantic import Field, JsonValue

from nexusflow.contracts.models import ActivityRef, Contract, Digest, Identifier


class DebugOutputInspection(Contract):
    session_id: Identifier
    workflow_id: Identifier
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    definition_digest: Digest
    node_id: Identifier
    activity_ref: ActivityRef
    values: dict[str, JsonValue]
    redacted_paths: tuple[str, ...]
    truncated: bool
