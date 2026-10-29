"""Small deterministic transport types safe to import inside Temporal workflows."""

from dataclasses import dataclass
from typing import Literal

DebugEventKind = Literal[
    "step_started",
    "paused",
    "waiting",
    "step_completed",
    "command_accepted",
    "completed",
    "failed",
    "cancelled",
]
DebugEventState = Literal[
    "starting",
    "running",
    "paused",
    "waiting_timer",
    "waiting_approval",
    "creating_task",
    "completed",
    "failed",
    "cancelling",
    "cancelled",
]
DEBUG_EVENT_RETENTION = 256
DEBUG_EVENT_PAGE_LIMIT = 25


@dataclass(frozen=True)
class ReleaseBinding:
    organization_id: str
    environment_id: str
    release_id: str
    release_digest: str
    configuration_revision: str
    runtime_version: str = "proof.v1"


@dataclass(frozen=True)
class Step:
    node_id: str
    kind: str
    input_json: str = "{}"
    activity_id: str = ""
    activity_version: str = ""
    artifact_digest: str = ""
    timeout_seconds: int = 30
    maximum_attempts: int = 1
    delay_seconds: float = 0
    task_approval_json: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    binding: ReleaseBinding
    workflow_id: str
    definition_digest: str
    steps: tuple[Step, ...]
    input_json: str = "{}"
    debug: bool = False
    breakpoints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivityCall:
    binding: ReleaseBinding
    node_id: str
    operation_id: str
    activity_id: str
    activity_version: str
    artifact_digest: str
    input_json: str
    workflow_input_json: str


@dataclass(frozen=True)
class ApprovalCommand:
    command_id: str
    node_id: str
    approved: bool
    expected_revision: int


@dataclass(frozen=True)
class TaskCreationCall:
    binding: ReleaseBinding
    execution_id: str
    temporal_run_id: str
    node_invocation_id: str
    definition_digest: str
    approval_json: str


@dataclass(frozen=True)
class TaskCompletionCommand:
    completion_id: str
    task_id: str
    organization_id: str
    environment_id: str
    workflow_id: str
    temporal_run_id: str
    node_invocation_id: str
    definition_digest: str
    definition_revision: str
    outcome: str
    task_revision: int


@dataclass(frozen=True)
class DebugCommand:
    command_id: str
    node_id: str
    action: str
    expected_revision: int


@dataclass(frozen=True)
class DebugBreakpointCommand:
    command_id: str
    node_id: str
    breakpoints: tuple[str, ...]
    expected_revision: int


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    revision: int


@dataclass(frozen=True)
class Snapshot:
    release_id: str
    definition_digest: str
    runtime_version: str
    node_id: str
    state: str
    revision: int
    completed_nodes: tuple[str, ...]


@dataclass(frozen=True)
class DebugStepOutput:
    snapshot: Snapshot
    node_id: str
    output_json: str


@dataclass(frozen=True)
class DebugBreakpointState:
    snapshot: Snapshot
    breakpoints: tuple[str, ...]


@dataclass(frozen=True)
class DebugRuntimeEvent:
    sequence: int
    kind: str
    node_id: str | None
    state: str
    revision: int


@dataclass(frozen=True)
class DebugEventQuery:
    after: int = 0
    limit: int = DEBUG_EVENT_PAGE_LIMIT


@dataclass(frozen=True)
class DebugEventBatch:
    snapshot: Snapshot
    events: tuple[DebugRuntimeEvent, ...]
    earliest_sequence: int
    latest_sequence: int
    next_after: int
    has_more: bool
    gap: bool


@dataclass(frozen=True)
class ExecutionResult:
    binding: ReleaseBinding
    definition_digest: str
    completed_nodes: tuple[str, ...]
    activity_outputs: dict[str, str]
