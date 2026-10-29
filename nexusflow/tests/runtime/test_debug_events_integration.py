"""Actual Temporal metadata event ordering, retention, restart and replay."""

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import (
    WorkflowFailureError,
    WorkflowQueryFailedError,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from nexusflow.modules.durable_runtime.types import (
    DebugCommand,
    DebugEventQuery,
    ExecutionPlan,
    ReleaseBinding,
    Step,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.runtime.test_temporal_integration import Effects, temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def event_plan(*, debug=True, count=2):
    return ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "cfg"),
        "workflow",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            *(Step(f"wait-{index}", "wait") for index in range(count)),
            Step("end", "end"),
        ),
        input_json='{"private":"never-show-input"}',
        debug=debug,
    )


async def test_events_are_ordered_readonly_and_restore_after_worker_restart(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "events-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            event_plan(),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "paused", "wait-0")
        initial = await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
        assert [event.kind for event in initial.events] == [
            "step_started",
            "step_completed",
            "paused",
        ]
        assert initial.events[-1].node_id == "wait-0" and initial.events[-1].revision == 0
        assert [event.sequence for event in initial.events] == [1, 2, 3]
        assert not initial.gap and not initial.has_more
        assert await handle.query(ProofWorkflow.debug_events, DebugEventQuery()) == initial
        assert "never-show-input" not in str(asdict(initial))
        command = DebugCommand("step", "wait-0", "step_over", 0)
        receipt = await handle.execute_update(ProofWorkflow.control, command)
        await wait_state(handle, "paused", "wait-1")
        stepped = await handle.query(
            ProofWorkflow.debug_events, DebugEventQuery(initial.next_after)
        )
        assert [event.kind for event in stepped.events] == [
            "command_accepted",
            "step_started",
            "waiting",
            "step_completed",
            "paused",
        ]
        assert stepped.events[-2].state == "running"
        assert stepped.events[0].revision == receipt.revision == 1
        assert await handle.execute_update(ProofWorkflow.control, command) == receipt
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("invalid", "wait-1", "continue", 0)
            )
        assert (
            await handle.query(ProofWorkflow.debug_events, DebugEventQuery(initial.next_after))
            == stepped
        )
    async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
        assert (
            await handle.query(ProofWorkflow.debug_events, DebugEventQuery(initial.next_after))
            == stepped
        )
        await handle.execute_update(
            ProofWorkflow.control, DebugCommand("finish", "wait-1", "continue", 1)
        )
        await asyncio.wait_for(handle.result(), timeout=20)
        completed = await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
        assert (
            completed.events[-1].kind == "completed" and completed.events[-1].state == "completed"
        )
        assert completed.snapshot.state == "completed"
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_retained_tail_gap_and_paging_are_bounded(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "event-retention-" + uuid4().hex
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[Effects().dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            replace(
                event_plan(count=98),
                steps=(
                    Step("start", "start"),
                    *(Step(f"work-{i}", "activity") for i in range(98)),
                    Step("end", "end"),
                ),
            ),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        # Activity boundaries avoid the dev server's one-second timer granularity.
        # Each distinct step/control emits four metadata events, overflowing 256.
        for index in range(98):
            await wait_state(handle, "paused", f"work-{index}")
            await handle.execute_update(
                ProofWorkflow.control,
                DebugCommand(f"step-{index}", f"work-{index}", "step_over", index),
            )
        await asyncio.wait_for(handle.result(), timeout=20)
        batch = await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
        assert batch.gap and len(batch.events) == 25 and batch.latest_sequence > 256
        assert batch.earliest_sequence == batch.latest_sequence - 255
        all_events = list(batch.events)
        while batch.has_more:
            batch = await handle.query(
                ProofWorkflow.debug_events, DebugEventQuery(batch.next_after)
            )
            assert not batch.gap and len(batch.events) <= 25
            all_events.extend(batch.events)
        assert len(all_events) == 256
        assert [event.sequence for event in all_events] == list(
            range(batch.earliest_sequence, batch.latest_sequence + 1)
        )
        assert all_events[-1].kind == "completed"
        tail = await handle.query(ProofWorkflow.debug_events, DebugEventQuery(batch.next_after))
        assert tail.events == () and not tail.has_more and not tail.gap
        with pytest.raises(WorkflowQueryFailedError):
            await handle.query(
                ProofWorkflow.debug_events, DebugEventQuery(batch.latest_sequence + 1)
            )
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_normal_execution_event_query_is_denied(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "event-normal-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
        handle = await client.start_workflow(
            ProofWorkflow.run, event_plan(debug=False), id=uuid4().hex, task_queue=queue
        )
        await handle.result()
        with pytest.raises(WorkflowQueryFailedError):
            await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


@activity.defn(name="nexusflow.proof.dispatch")
async def private_failure(_call) -> str:
    raise ApplicationError("never-show-error-secret", non_retryable=True)


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
async def test_terminal_events_never_contain_exception_text(temporal_env, terminal):  # noqa: F811
    client = temporal_env.client
    queue = "event-terminal-" + uuid4().hex
    value = event_plan(count=1)
    value = replace(
        value, steps=(Step("start", "start"), Step("effect", "activity"), Step("end", "end"))
    )
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[private_failure]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run, value, id=uuid4().hex, task_queue=queue
        )
        await wait_state(handle, "paused", "effect")
        if terminal == "failed":
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("run", "effect", "continue", 0)
            )
        else:
            await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=20)
        batch = await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
        assert batch.events[-1].kind == terminal and batch.snapshot.state == terminal
        assert "never-show-error-secret" not in str(asdict(batch))
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
