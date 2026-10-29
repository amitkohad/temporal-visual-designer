"""Paused breakpoint replacement contracts; the source plan remains immutable."""

from typing import Annotated

from pydantic import BeforeValidator, Field, field_validator

from nexusflow.contracts.models import Contract, Identifier

BreakpointIds = Annotated[
    tuple[Identifier, ...],
    BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    Field(max_length=100),
]


class DebugBreakpointControl(Contract):
    command_id: Identifier
    node_id: Identifier
    breakpoints: BreakpointIds
    expected_revision: Annotated[int, Field(ge=0, lt=2**63)]

    @field_validator("breakpoints")
    @classmethod
    def canonical_set(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate breakpoint")
        return tuple(sorted(value))


class DebugBreakpoints(Contract):
    session_id: Identifier
    revision: Annotated[int, Field(ge=0, lt=2**63)]
    breakpoints: BreakpointIds
