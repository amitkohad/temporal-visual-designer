"""A versioned, sandboxed Temporal proof interpreter with durable control updates."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from nexusflow.modules.durable_runtime.types import (
    DEBUG_EVENT_PAGE_LIMIT,
    DEBUG_EVENT_RETENTION,
    ActivityCall,
    ApprovalCommand,
    CommandReceipt,
    DebugBreakpointCommand,
    DebugBreakpointState,
    DebugCommand,
    DebugEventBatch,
    DebugEventQuery,
    DebugReconnectState,
    DebugRuntimeEvent,
    DebugStepOutput,
    ExecutionPlan,
    ExecutionResult,
    Snapshot,
    TaskCompletionCommand,
    TaskCreationCall,
)


@workflow.defn(name="NexusFlowProofV1")
class ProofWorkflow:
    def __init__(self) -> None:
        self.plan: ExecutionPlan | None = None
        self.node_id = ""
        self.state = "starting"
        self.revision = 0
        self.completed: list[str] = []
        self.approved: bool | None = None
        self.permit = ""
        self.stepping = True
        self.breakpoints: tuple[str, ...] = ()
        self.receipts: dict[
            str, tuple[ApprovalCommand | DebugCommand | DebugBreakpointCommand, CommandReceipt]
        ] = {}
        self.task_receipts: dict[str, tuple[TaskCompletionCommand, CommandReceipt]] = {}
        self.managed_approval = False
        self.managed_task_id = ""
        self.cancellation_requested = False
        self.activity_outputs: dict[str, str] = {}
        self.event_sequence = 0
        self.event_log: list[DebugRuntimeEvent] = []

    @workflow.run
    async def run(self, plan: ExecutionPlan) -> ExecutionResult:
        if plan.binding.runtime_version != "proof.v1" or not 2 <= len(plan.steps) <= 100:
            raise ApplicationError("Unsupported execution plan", non_retryable=True)
        if plan.debug and plan.binding.environment_id != "dev":
            raise ApplicationError("Debug is restricted to Dev", non_retryable=True)
        self.plan = plan
        self.breakpoints = plan.breakpoints
        outputs = self.activity_outputs
        try:
            for step in plan.steps:
                self.node_id = step.node_id
                if step.kind not in {"start", "end", "activity", "approval", "wait"}:
                    raise ApplicationError("Unsupported step", non_retryable=True)
                if (
                    plan.debug
                    and step.kind not in {"start", "end"}
                    and (self.stepping or step.node_id in self.breakpoints)
                ):
                    self.state = "paused"
                    self._event("paused")
                    await workflow.wait_condition(lambda: self.permit == self.node_id)
                    self.permit = ""
                self.state = "running"
                self._event("step_started")
                if step.kind == "activity":
                    call = ActivityCall(
                        binding=plan.binding,
                        node_id=step.node_id,
                        operation_id=f"{workflow.info().workflow_id}:{step.node_id}",
                        activity_id=step.activity_id,
                        activity_version=step.activity_version,
                        artifact_digest=step.artifact_digest,
                        input_json=step.input_json,
                        workflow_input_json=plan.input_json,
                    )
                    output = await workflow.execute_activity(
                        "nexusflow.proof.dispatch",
                        call,
                        result_type=str,
                        activity_id=step.node_id,
                        start_to_close_timeout=timedelta(seconds=step.timeout_seconds),
                        retry_policy=RetryPolicy(maximum_attempts=step.maximum_attempts),
                    )
                    if len(output.encode()) > 65536:
                        raise ApplicationError(
                            "Activity output exceeds proof limit", non_retryable=True
                        )
                    outputs[step.node_id] = output
                    if sum(len(value.encode()) for value in outputs.values()) > 131072:
                        raise ApplicationError(
                            "Combined outputs exceed proof limit", non_retryable=True
                        )
                elif step.kind == "approval":
                    self.approved = None
                    self.managed_approval = bool(step.task_approval_json)
                    self.managed_task_id = ""
                    if self.managed_approval:
                        self.state = "creating_task"
                        self._event("waiting")
                        task_id = await workflow.execute_activity(
                            "nexusflow.tasks.create",
                            TaskCreationCall(
                                binding=plan.binding,
                                execution_id=workflow.info().workflow_id,
                                temporal_run_id=workflow.info().run_id,
                                node_invocation_id=step.node_id,
                                definition_digest=plan.definition_digest,
                                approval_json=step.task_approval_json,
                            ),
                            result_type=str,
                            activity_id=f"task-{step.node_id}",
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        if (
                            not task_id
                            or len(task_id) > 128
                            or any(
                                not (c.isascii() and (c.isalnum() or c in "_.-")) for c in task_id
                            )
                        ):
                            raise ApplicationError("Invalid created task ID", non_retryable=True)
                        self.managed_task_id = task_id
                    self.state = "waiting_approval"
                    self._event("waiting")
                    await workflow.wait_condition(lambda: self.approved is not None)
                    if not self.approved:
                        raise ApplicationError(
                            "Approval rejected", type="ApprovalRejected", non_retryable=True
                        )
                elif step.kind == "wait":
                    self.state = "waiting_timer"
                    self._event("waiting")
                    await workflow.sleep(timedelta(seconds=step.delay_seconds))
                if self.cancellation_requested:
                    # A timer may finish after cancel acceptance but before the
                    # adapter's separate cancellation RPC. Keep its original
                    # node/state and block completion or any downstream dispatch.
                    await workflow.wait_condition(lambda: not self.cancellation_requested)
                self.completed.append(step.node_id)
                self._event("step_completed", state="running")
            self.state = "completed"
            self._event("completed")
            return ExecutionResult(
                plan.binding, plan.definition_digest, tuple(self.completed), outputs
            )
        except asyncio.CancelledError:
            self.state = "cancelled"
            self._event("cancelled")
            raise
        except Exception:
            self.state = "failed"
            self._event("failed")
            raise

    def _duplicate(
        self, command: ApprovalCommand | DebugCommand | DebugBreakpointCommand
    ) -> CommandReceipt | None:
        previous = self.receipts.get(command.command_id)
        if previous is not None:
            if previous[0] != command:
                raise ApplicationError("Command ID reused with different content", type="Conflict")
            return previous[1]
        if not command.command_id or len(self.receipts) >= 1000:
            raise ApplicationError(
                "Command limit exceeded or empty command ID", type="InvalidArgument"
            )
        if command.expected_revision != self.revision:
            raise ApplicationError("Stale command revision", type="Conflict")
        return None

    def _accept(
        self, command: ApprovalCommand | DebugCommand | DebugBreakpointCommand
    ) -> CommandReceipt:
        self.revision += 1
        receipt = CommandReceipt(command.command_id, self.revision)
        self.receipts[command.command_id] = (command, receipt)
        self._event("command_accepted")
        return receipt

    @workflow.update
    def approve(self, command: ApprovalCommand) -> CommandReceipt:
        previous = self._duplicate(command)
        if previous is not None:
            return previous
        if (
            self.state != "waiting_approval"
            or self.managed_approval
            or command.node_id != self.node_id
            or self.approved is not None
        ):
            raise ApplicationError("Approval does not match the waiting step", type="Conflict")
        self.approved = command.approved
        return self._accept(command)

    def _validate_task_completion(self, command: TaskCompletionCommand) -> CommandReceipt | None:
        plan = self.plan
        info = workflow.info()
        if (
            plan is None
            or (command.organization_id, command.environment_id)
            != (plan.binding.organization_id, plan.binding.environment_id)
            or command.workflow_id != info.workflow_id
            or command.temporal_run_id != info.run_id
            or command.definition_digest != plan.definition_digest
            or command.definition_revision != plan.binding.release_id
            or command.outcome not in {"approved", "rejected"}
            or not command.completion_id
            or not command.task_id
            or type(command.task_revision) is not int
            or command.task_revision < 2
        ):
            raise ApplicationError("Task completion binding is invalid", type="Conflict")
        previous = self.task_receipts.get(command.completion_id)
        if previous is not None:
            if previous[0] != command:
                raise ApplicationError(
                    "Completion ID reused with different content", type="Conflict"
                )
            return previous[1]
        if (
            len(self.task_receipts) >= 100
            or self.state != "waiting_approval"
            or command.node_invocation_id != self.node_id
            or (self.managed_approval and command.task_id != self.managed_task_id)
            or self.approved is not None
        ):
            raise ApplicationError("Task completion does not match waiting step", type="Conflict")
        return None

    @workflow.update
    def complete_task(self, command: TaskCompletionCommand) -> CommandReceipt:
        previous = self._validate_task_completion(command)
        if previous is not None:
            return previous
        self.approved = command.outcome == "approved"
        self.revision += 1
        receipt = CommandReceipt(command.completion_id, self.revision)
        self.task_receipts[command.completion_id] = (command, receipt)
        self._event("command_accepted")
        return receipt

    @complete_task.validator
    def validate_complete_task(self, command: TaskCompletionCommand) -> None:
        # Reject before acceptance: an early future-node delivery must not
        # permanently poison its stable update ID with a failed outcome.
        self._validate_task_completion(command)

    @workflow.update
    def control(self, command: DebugCommand) -> CommandReceipt:
        previous = self._duplicate(command)
        if previous is not None:
            return previous
        if (
            self.plan is None
            or not self.plan.debug
            or self.plan.binding.environment_id != "dev"
            or command.node_id != self.node_id
        ):
            raise ApplicationError("Debug command requires a matching Dev step", type="Conflict")
        if command.action not in {"step_over", "continue", "cancel"} or self.permit:
            raise ApplicationError("Invalid or conflicting debug command", type="Conflict")
        if command.action == "cancel":
            if self.state not in {"paused", "waiting_timer"} and not (
                self.state == "waiting_approval"
                and not self.managed_approval
                and self.approved is None
            ):
                raise ApplicationError("Debug step cannot be cancelled", type="Conflict")
            if self.state != "paused" and not workflow.patched("debug-wait-cancellation-v1"):
                # Older histories may contain accepted updates whose handlers
                # rejected cancellation at waits. Preserve that outcome on replay.
                raise ApplicationError("Debug step cannot be cancelled", type="Conflict")
            # Keep this boundary blocked until the exact-run adapter requests
            # actual Temporal cancellation; never manufacture a human response.
            self.cancellation_requested = True
            self.state = "cancelling"
            return self._accept(command)
        if self.state != "paused":
            raise ApplicationError("Debug command requires a paused Dev step", type="Conflict")
        self.stepping = command.action == "step_over"
        self.permit = command.node_id
        return self._accept(command)

    @workflow.update
    def replace_breakpoints(self, command: DebugBreakpointCommand) -> CommandReceipt:
        if (
            type(command.command_id) is not str
            or not 1 <= len(command.command_id) <= 128
            or any(not (c.isascii() and (c.isalnum() or c in "_.-")) for c in command.command_id)
            or type(command.expected_revision) is not int
            or not 0 <= command.expected_revision < 2**63
        ):
            raise ApplicationError("Invalid breakpoint command", type="Conflict")
        previous = self._duplicate(command)
        if previous is not None:
            return previous
        plan = self.plan
        if (
            plan is None
            or not plan.debug
            or plan.binding.environment_id != "dev"
            or self.state != "paused"
            or self.permit
            or command.node_id != self.node_id
        ):
            raise ApplicationError("Breakpoint change requires a paused Dev step", type="Conflict")
        executable = {
            step.node_id for step in plan.steps if step.kind in {"activity", "approval", "wait"}
        }
        targets = command.breakpoints
        if (
            type(targets) is not tuple
            or len(targets) > 100
            or any(type(node) is not str for node in targets)
            or tuple(sorted(set(targets))) != targets
            or any(node not in executable for node in targets)
        ):
            raise ApplicationError("Invalid breakpoint targets", type="Conflict")
        # Changing the overlay never releases the current paused invocation.
        self.breakpoints = targets
        return self._accept(command)

    def _event(self, kind: str, *, state: str | None = None) -> None:
        plan = self.plan
        if plan is None or not plan.debug or plan.binding.environment_id != "dev":
            return
        self.event_sequence += 1
        self.event_log.append(
            DebugRuntimeEvent(
                self.event_sequence, kind, self.node_id or None, state or self.state, self.revision
            )
        )
        if len(self.event_log) > DEBUG_EVENT_RETENTION:
            del self.event_log[:-DEBUG_EVENT_RETENTION]

    @workflow.query
    def debug_reconnect(self) -> DebugReconnectState:
        plan = self.plan
        if plan is None or not plan.debug or plan.binding.environment_id != "dev":
            raise ApplicationError("Debug reconnect is unavailable", type="Conflict")
        return DebugReconnectState(
            self.snapshot(),
            tuple(sorted(set(self.breakpoints))),
            self.event_log[0].sequence if self.event_log else 0,
            self.event_sequence,
        )

    @workflow.query
    def debug_events(self, query: DebugEventQuery) -> DebugEventBatch:
        plan = self.plan
        if plan is None or not plan.debug or plan.binding.environment_id != "dev":
            raise ApplicationError("Debug events are unavailable", type="Conflict")
        if (
            type(query.after) is not int
            or not 0 <= query.after < 2**63
            or type(query.limit) is not int
            or not 1 <= query.limit <= DEBUG_EVENT_PAGE_LIMIT
            or query.after > self.event_sequence
        ):
            raise ApplicationError("Invalid debug event cursor", type="InvalidArgument")
        earliest = self.event_log[0].sequence if self.event_log else 0
        gap = bool(earliest and query.after < earliest - 1)
        events = tuple(event for event in self.event_log if event.sequence > query.after)[
            : query.limit
        ]
        next_after = events[-1].sequence if events else query.after
        return DebugEventBatch(
            self.snapshot(),
            events,
            earliest,
            self.event_sequence,
            next_after,
            next_after < self.event_sequence,
            gap,
        )

    @workflow.query
    def debug_breakpoints(self) -> DebugBreakpointState:
        plan = self.plan
        if plan is None or not plan.debug or plan.binding.environment_id != "dev":
            raise ApplicationError("Debug breakpoints are unavailable", type="Conflict")
        return DebugBreakpointState(self.snapshot(), tuple(sorted(set(self.breakpoints))))

    @workflow.query
    def step_output(self, node_id: str) -> DebugStepOutput:
        plan = self.plan
        if (
            plan is None
            or not plan.debug
            or plan.binding.environment_id != "dev"
            or node_id not in self.completed
            or not any(step.node_id == node_id and step.kind == "activity" for step in plan.steps)
            or node_id not in self.activity_outputs
        ):
            raise ApplicationError("Debug step output is unavailable", type="Conflict")
        return DebugStepOutput(self.snapshot(), node_id, self.activity_outputs[node_id])

    @workflow.query
    def snapshot(self) -> Snapshot:
        plan = self.plan
        return Snapshot(
            release_id=plan.binding.release_id if plan else "",
            definition_digest=plan.definition_digest if plan else "",
            runtime_version=plan.binding.runtime_version if plan else "",
            node_id=self.node_id,
            state=self.state,
            revision=self.revision,
            completed_nodes=tuple(self.completed),
        )
