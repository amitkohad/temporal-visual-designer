"""Public contracts and trusted runtime port for Dev debugging."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import BeforeValidator, Field, JsonValue

from nexusflow.contracts.models import Contract, Digest, Identifier
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    DebugBreakpointCommand,
    DebugBreakpointState,
    DebugCommand,
    DebugEventBatch,
    DebugEventQuery,
    DebugStepOutput,
    ExecutionPlan,
    Snapshot,
)
from nexusflow.modules.identity_environments.types import Environment


class DebugStart(Contract):
    operation_id: Identifier
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    workflow_id: Identifier
    configuration_revision: Identifier
    input: dict[str, JsonValue] = Field(default_factory=dict)
    breakpoints: Annotated[
        tuple[Identifier, ...],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
        Field(max_length=100),
    ] = ()
    mode: Literal["real"]


class DebugSession(Contract):
    session_id: Identifier
    project_id: Identifier
    source_revision: int
    workflow_id: Identifier
    configuration_revision: Identifier
    definition_digest: Digest
    actor_id: str
    created_at: str
    execution_id: Identifier
    temporal_run_id: str | None
    state: Literal["dispatch_pending", "started"]


class DebugControl(Contract):
    command_id: Identifier
    node_id: Identifier
    action: Literal["step_over", "continue", "cancel"]
    expected_revision: Annotated[int, Field(ge=0, lt=2**63)]


class DebugStatus(Contract):
    session: DebugSession
    snapshot: Snapshot


class DebugRuntime(Protocol):
    async def start(
        self, placement: Environment, execution_id: str, plan: ExecutionPlan
    ) -> str: ...
    async def snapshot(
        self, placement: Environment, execution_id: str, run_id: str
    ) -> Snapshot: ...
    async def control(
        self, placement: Environment, execution_id: str, run_id: str, command: DebugCommand
    ) -> CommandReceipt: ...
    async def step_output(
        self, placement: Environment, execution_id: str, run_id: str, node_id: str
    ) -> DebugStepOutput: ...
    async def breakpoints(
        self, placement: Environment, execution_id: str, run_id: str
    ) -> DebugBreakpointState: ...
    async def replace_breakpoints(
        self,
        placement: Environment,
        execution_id: str,
        run_id: str,
        command: DebugBreakpointCommand,
    ) -> CommandReceipt: ...
    async def events(
        self, placement: Environment, execution_id: str, run_id: str, query: DebugEventQuery
    ) -> DebugEventBatch: ...


class DebugError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DebugRecord:
    request: DebugStart
    session: DebugSession
    placement: Environment
    plan: ExecutionPlan
