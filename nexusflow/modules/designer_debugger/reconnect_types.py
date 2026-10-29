"""One payload-free runtime snapshot and its exact replay cursor."""

from typing import Annotated

from pydantic import Field

from nexusflow.contracts.models import Contract, Identifier
from nexusflow.modules.designer_debugger.event_types import Sequence
from nexusflow.modules.durable_runtime.types import Snapshot

MAX_RECONNECT_BYTES = 32768


class DebugReconnect(Contract):
    session_id: Identifier
    snapshot: Snapshot
    breakpoints: Annotated[tuple[Identifier, ...], Field(max_length=100)]
    earliest_sequence: Sequence
    latest_sequence: Sequence
    next_after: Sequence
