"""Real Temporal cancellation at durable timer and unmanaged approval waits."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.worker import Replayer, Worker

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    ApprovalCommand,
    CommandReceipt,
    DebugCommand,
    ExecutionPlan,
    ReleaseBinding,
    Step,
    TaskCreationCall,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_temporal_integration import Effects, temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@workflow.defn(name="NexusFlowProofV1")
class LegacyPausedCancellationWorkflow(ProofWorkflow):
    """Pre-wait-cancellation control used to produce an actual legacy history."""

    @workflow.run
    async def run(self, plan: ExecutionPlan):
        return await super().run(plan)

    @workflow.update
    def control(self, command: DebugCommand) -> CommandReceipt:
        previous = self._duplicate(command)
        if previous is not None:
            return previous
        if (
            self.plan is None
            or not self.plan.debug
            or self.state != "paused"
            or command.node_id != self.node_id
        ):
            raise ApplicationError("Debug command requires a paused Dev step", type="Conflict")
        if command.action not in {"step_over", "continue", "cancel"} or self.permit:
            raise ApplicationError("Invalid or conflicting debug command", type="Conflict")
        if command.action == "cancel":
            self.state = "cancelling"
            return self._accept(command)
        self.stepping = command.action == "step_over"
        self.permit = command.node_id
        return self._accept(command)


def wait_plan(kind, *, managed=False):
    return ExecutionPlan(
        ReleaseBinding("acme", "dev", "debug-wait", "sha256:" + "a" * 64, "cfg"),
        "workflow",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            Step("waiting", kind, delay_seconds=2, task_approval_json="{}" if managed else ""),
            Step("effect", "activity"),
            Step("end", "end"),
        ),
        debug=True,
    )


async def timer_has_fired(handle):
    async def observe():
        while True:
            history = await handle.fetch_history()
            if any(
                event.event_type == EventType.EVENT_TYPE_TIMER_FIRED for event in history.events
            ):
                return
            await asyncio.sleep(0.02)

    await asyncio.wait_for(observe(), timeout=20)


@pytest.mark.parametrize("kind", ["wait", "approval"])
async def test_wait_cancel_preserves_frontier_through_restart_and_delayed_delivery(
    temporal_env,  # noqa: F811
    tmp_path,
    kind,
):
    client = temporal_env.client
    queue, execution = "wait-cancel-" + uuid4().hex, "wait-cancel-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    effects = Effects()
    journal = SQLiteDispatchJournal(tmp_path / "dispatch.db")

    async def clients(requested):
        assert requested == placement
        return client

    runtime = TemporalDebugRuntime(clients, journal)
    try:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            started = await client.start_workflow(
                ProofWorkflow.run,
                wait_plan(kind),
                id=execution,
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            run_id = started.first_execution_run_id
            assert run_id is not None
            handle = client.get_workflow_handle(execution, run_id=run_id)
            await wait_state(handle, "paused", "waiting")
            await runtime.control(
                placement, execution, run_id, DebugCommand("go", "waiting", "continue", 0)
            )
            state = "waiting_timer" if kind == "wait" else "waiting_approval"
            waiting = await wait_state(handle, state, "waiting")
            assert waiting.completed_nodes == ("start",) and waiting.revision == 1
            for command in (
                DebugCommand("wrong-node", "effect", "cancel", 1),
                DebugCommand("stale", "waiting", "cancel", 0),
                DebugCommand("step-wait", "waiting", "step_over", 1),
            ):
                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update(ProofWorkflow.control, command)
            cancel = DebugCommand("cancel", "waiting", "cancel", 1)
            receipt = await handle.execute_update(ProofWorkflow.control, cancel)
            assert receipt.revision == 2
            pending = await wait_state(handle, "cancelling", "waiting")
            assert pending.completed_nodes == ("start",)
            if kind == "approval":
                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update(
                        ProofWorkflow.approve, ApprovalCommand("late-approval", "waiting", True, 2)
                    )
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            if kind == "wait":
                # Observe the actual timer event, rather than assume a wall-clock
                # delay. Accepted cancellation must still keep the frontier closed.
                await timer_has_fired(handle)
            pending = await wait_state(handle, "cancelling", "waiting")
            assert pending.completed_nodes == ("start",) and pending.revision == 2
            assert effects.calls == []
            assert await runtime.control(placement, execution, run_id, cancel) == receipt
            with pytest.raises(WorkflowFailureError) as failure:
                await asyncio.wait_for(handle.result(), timeout=20)
            assert isinstance(failure.value.cause, CancelledError)
            assert (await handle.describe()).status == WorkflowExecutionStatus.CANCELED
            snapshot = await handle.query(ProofWorkflow.snapshot)
            assert snapshot.state == "cancelled"
            assert snapshot.node_id == "waiting" and snapshot.completed_nodes == ("start",)
            assert effects.calls == []
            assert await runtime.control(placement, execution, run_id, cancel) == receipt
        history = await handle.fetch_history()
        assert any(
            event.event_type == EventType.EVENT_TYPE_MARKER_RECORDED for event in history.events
        )
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)
    finally:
        journal.close()


@activity.defn(name="nexusflow.tasks.create")
async def created_task(call: TaskCreationCall) -> str:
    return "managed-task"


async def test_managed_task_wait_rejects_cancel_without_fabricating_completion(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "managed-cancel-guard-" + uuid4().hex
    effects = Effects()
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch, created_task],
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            wait_plan("approval", managed=True),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "paused", "waiting")
        await handle.execute_update(
            ProofWorkflow.control, DebugCommand("go", "waiting", "continue", 0)
        )
        waiting = await wait_state(handle, "waiting_approval", "waiting")
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("cancel", "waiting", "cancel", 1)
            )
        assert await handle.query(ProofWorkflow.snapshot) == waiting
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert effects.calls == []
        # Test teardown uses an operator cancellation, not the unsupported debug
        # operation, and does not claim managed task cleanup.
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_preincrement_rejected_wait_cancel_history_replays_unchanged(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "legacy-wait-cancel-" + uuid4().hex
    async with Worker(
        client,
        task_queue=queue,
        workflows=[LegacyPausedCancellationWorkflow],
        activities=[Effects().dispatch],
    ):
        handle = await client.start_workflow(
            LegacyPausedCancellationWorkflow.run,
            wait_plan("approval"),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "paused", "waiting")
        await handle.execute_update(
            LegacyPausedCancellationWorkflow.control, DebugCommand("go", "waiting", "continue", 0)
        )
        waiting = await wait_state(handle, "waiting_approval", "waiting")
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                LegacyPausedCancellationWorkflow.control,
                DebugCommand("old-rejected", "waiting", "cancel", 1),
            )
        assert await handle.query(ProofWorkflow.snapshot) == waiting
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[Effects().dispatch],
    ):
        assert await handle.query(ProofWorkflow.snapshot) == waiting
        # Replaying a historical rejected cancellation selects the old patch
        # branch for this run. Migration must preserve that rejection rather
        # than convert a formerly failed update into an accepted cancellation.
        for command_id in ("old-rejected", "after-migration"):
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    ProofWorkflow.control,
                    DebugCommand(command_id, "waiting", "cancel", 1),
                )
        assert await handle.query(ProofWorkflow.snapshot) == waiting
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    history = await handle.fetch_history()
    assert not any(
        event.event_type == EventType.EVENT_TYPE_MARKER_RECORDED for event in history.events
    )
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)
