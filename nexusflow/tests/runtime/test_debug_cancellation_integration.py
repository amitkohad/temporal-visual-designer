"""Actual Temporal cancellation after a durable, paused-only debug command."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import CancelledError
from temporalio.worker import Replayer, Worker

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    DebugCommand,
    ExecutionPlan,
    ReleaseBinding,
    Step,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_temporal_integration import Effects, temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def cancellation_plan(*, debug=True, seconds=0):
    return ExecutionPlan(
        ReleaseBinding("acme", "dev", "debug-release", "sha256:" + "a" * 64, "config-1"),
        "workflow",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            Step("timer", "wait", delay_seconds=seconds),
            Step("effect", "activity"),
            Step("approval", "approval"),
            Step("end", "end"),
        ),
        debug=debug,
    )


@pytest.mark.parametrize("advance", [False, True])
async def test_cancel_closes_exact_run_without_downstream_effects_and_replays(
    temporal_env,  # noqa: F811
    tmp_path,
    advance,
):
    client = temporal_env.client
    queue = "debug-cancel-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")

    async def clients(requested):
        assert requested == placement
        return client

    runtime = TemporalDebugRuntime(clients, journal)
    effects = Effects()
    execution_id = "cancel-" + uuid4().hex
    try:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            started = await client.start_workflow(
                ProofWorkflow.run,
                cancellation_plan(),
                id=execution_id,
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            run_id = started.first_execution_run_id
            assert run_id is not None
            handle = client.get_workflow_handle(execution_id, run_id=run_id)
            await wait_state(handle, "paused", "timer")
            revision, node = 0, "timer"
            if advance:
                await runtime.control(
                    placement, execution_id, run_id, DebugCommand("step", "timer", "step_over", 0)
                )
                await wait_state(handle, "paused", "effect")
                revision, node = 1, "effect"
            command = DebugCommand("cancel", node, "cancel", revision)
            # Simulate an API outage after Temporal committed the update but
            # before the adapter issued its separate cancellation request.
            receipt = await handle.execute_update(ProofWorkflow.control, command)
            pending = await wait_state(handle, "cancelling", node)
            assert pending.revision == receipt.revision == revision + 1
            assert effects.calls == []
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    ProofWorkflow.control,
                    DebugCommand("too-late", node, "continue", receipt.revision),
                )
        # The pending cancel remains blocked across worker replacement. A
        # retry reconciles its durable update receipt and cancels that same run.
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            await wait_state(handle, "cancelling", node)
            recovered = await runtime.control(placement, execution_id, run_id, command)
            assert recovered == receipt
            with pytest.raises(WorkflowFailureError) as failure:
                await asyncio.wait_for(handle.result(), timeout=20)
            assert isinstance(failure.value.cause, CancelledError)
            assert (await handle.describe()).status == WorkflowExecutionStatus.CANCELED
            snapshot = await handle.query(ProofWorkflow.snapshot)
            assert snapshot.state == "cancelled"
            assert snapshot.completed_nodes == (("start", "timer") if advance else ("start",))
            assert effects.calls == []
            assert await runtime.control(placement, execution_id, run_id, command) == receipt
            with pytest.raises(DebugError):
                await runtime.control(
                    placement,
                    execution_id,
                    run_id,
                    DebugCommand("cancel", node, "continue", revision),
                )
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        journal.close()


async def test_cancel_update_rejects_normal_timer_without_closing_run(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "debug-cancel-guard-" + uuid4().hex
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[Effects().dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            cancellation_plan(debug=False, seconds=60),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        revision = 0
        await wait_state(handle, "waiting_timer", "timer")
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("cancel", "timer", "cancel", revision)
            )
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert (await handle.query(ProofWorkflow.snapshot)).revision == revision
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
