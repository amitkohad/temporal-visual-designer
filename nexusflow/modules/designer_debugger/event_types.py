"""Payload-free replay pages for one exact debug execution."""

from typing import Annotated

from pydantic import Field

from nexusflow.contracts.models import Contract, Identifier
from nexusflow.modules.durable_runtime.types import DebugEventKind, DebugEventState

Sequence = Annotated[int, Field(ge=0, lt=2**63)]


class DebugEvent(Contract):
    sequence: Annotated[int, Field(ge=1, lt=2**63)]
    kind: DebugEventKind
    node_id: Identifier | None
    state: DebugEventState
    revision: Sequence


class DebugEvents(Contract):
    session_id: Identifier
    events: Annotated[tuple[DebugEvent, ...], Field(max_length=25)]
    earliest_sequence: Sequence
    latest_sequence: Sequence
    next_after: Sequence
    has_more: bool
    gap: bool
