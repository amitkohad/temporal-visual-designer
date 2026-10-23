"""Descriptor and versioned mapping contracts for offline authoring validation."""

from typing import Literal, Protocol

from pydantic import Field

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, Contract, Identifier


class DescriptorCatalog(Protocol):
    def descriptor(self, reference: ActivityRef) -> ActivityDescriptor | None: ...


class Binding(Contract):
    schema_version: Literal["0.1"]
    source: Literal["input", "step"]
    node_id: Identifier | None = None
    path: str = Field(max_length=2048, pattern=r"^(/.*)?$")
