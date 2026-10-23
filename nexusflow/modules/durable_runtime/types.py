"""Small deterministic transport types safe to import inside Temporal workflows."""

from dataclasses import dataclass


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
class DebugCommand:
    command_id: str
    node_id: str
    action: str
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
class ExecutionResult:
    binding: ReleaseBinding
    definition_digest: str
    completed_nodes: tuple[str, ...]
    activity_outputs: dict[str, str]
