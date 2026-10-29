"""Atomic reconnect baselines resume exactly after their saved event cursor."""

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import WorkflowQueryFailedError
from temporalio.worker import Replayer, Worker

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    DebugBreakpointCommand,
    DebugCommand,
    DebugEventQuery,
    Step,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_debug_events_integration import event_plan
from nexusflow.tests.runtime.test_temporal_integration import Effects, temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_one_baseline_keeps_snapshot_breakpoints_and_cursor_coherent_across_restart(
    temporal_env,  # noqa: F811
    tmp_path,
):
    client = temporal_env.client
    queue, execution = "reconnect-" + uuid4().hex, "reconnect-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")

    async def clients(requested):
        assert requested == placement
        return client

    runtime = TemporalDebugRuntime(clients, journal)
    try:
        async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
            started = await client.start_workflow(
                ProofWorkflow.run,
                event_plan(),
                id=execution,
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            run_id = started.first_execution_run_id
            assert run_id is not None
            handle = client.get_workflow_handle(execution, run_id=run_id)
            paused = await wait_state(handle, "paused", "wait-0")
            original = await runtime.reconnect(placement, execution, run_id)
            assert original.snapshot == paused and original.latest_sequence == 3
            assert original.breakpoints == () and original.earliest_sequence == 1
            assert "never-show-input" not in str(asdict(original))
            assert await runtime.reconnect(placement, execution, run_id) == original
            changed = await runtime.replace_breakpoints(
                placement,
                execution,
                run_id,
                DebugBreakpointCommand("set", "wait-0", ("wait-1",), 0),
            )
            baseline = await runtime.reconnect(placement, execution, run_id)
            assert baseline.snapshot.state == "paused" and baseline.snapshot.node_id == "wait-0"
            assert baseline.snapshot.revision == changed.revision == 1
            assert baseline.breakpoints == ("wait-1",) and baseline.latest_sequence == 4
            assert baseline.snapshot.completed_nodes == ("start",)
            after = await runtime.events(
                placement, execution, run_id, DebugEventQuery(baseline.latest_sequence)
            )
            assert after.events == () and not after.has_more
        async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
            assert await runtime.reconnect(placement, execution, run_id) == baseline
            await runtime.control(
                placement, execution, run_id, DebugCommand("go", "wait-0", "continue", 1)
            )
            await wait_state(handle, "paused", "wait-1")
            page = await runtime.events(
                placement, execution, run_id, DebugEventQuery(baseline.latest_sequence)
            )
            assert [event.sequence for event in page.events] == list(
                range(baseline.latest_sequence + 1, page.latest_sequence + 1)
            )
            assert page.events[0].kind == "command_accepted" and page.events[-1].kind == "paused"
            # The earlier response is an immutable baseline, not aliases to live state.
            assert baseline.snapshot.revision == 1 and baseline.snapshot.node_id == "wait-0"
            assert baseline.latest_sequence == 4 and baseline.snapshot.completed_nodes == ("start",)
            current = await runtime.reconnect(placement, execution, run_id)
            assert current.latest_sequence == page.latest_sequence
            assert current.snapshot == page.snapshot
            await runtime.control(
                placement, execution, run_id, DebugCommand("finish", "wait-1", "continue", 2)
            )
            await asyncio.wait_for(handle.result(), timeout=20)
            terminal = await runtime.reconnect(placement, execution, run_id)
            assert terminal.snapshot.state == "completed"
            assert terminal.snapshot.completed_nodes == ("start", "wait-0", "wait-1", "end")
            assert terminal.latest_sequence > current.latest_sequence
            assert (
                await runtime.events(
                    placement, execution, run_id, DebugEventQuery(terminal.latest_sequence)
                )
            ).events == ()
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        journal.close()


async def test_normal_execution_cannot_query_debug_reconnect(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "normal-reconnect-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[ProofWorkflow]):
        handle = await client.start_workflow(
            ProofWorkflow.run, event_plan(debug=False), id=uuid4().hex, task_queue=queue
        )
        await asyncio.wait_for(handle.result(), timeout=20)
        with pytest.raises(WorkflowQueryFailedError):
            await handle.query(ProofWorkflow.debug_reconnect)
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_atomic_baseline_recovers_after_retention_gap(temporal_env):  # noqa: F811
    client = temporal_env.client
    queue = "retained-reconnect-" + uuid4().hex
    effects = Effects()
    value = replace(
        event_plan(),
        steps=(
            Step("start", "start"),
            *(Step(f"work-{index}", "activity") for index in range(98)),
            Step("end", "end"),
        ),
    )
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            value,
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        for index in range(98):
            await wait_state(handle, "paused", f"work-{index}")
            await handle.execute_update(
                ProofWorkflow.control,
                DebugCommand(f"step-{index}", f"work-{index}", "step_over", index),
            )
        await asyncio.wait_for(handle.result(), timeout=20)
        retained = await handle.query(ProofWorkflow.debug_events, DebugEventQuery())
        assert retained.gap
        baseline = await handle.query(ProofWorkflow.debug_reconnect)
        assert baseline.snapshot.state == "completed"
        assert len(baseline.snapshot.completed_nodes) == 100
        assert baseline.snapshot.revision == 98
        assert baseline.latest_sequence == retained.latest_sequence > 256
        assert baseline.earliest_sequence == baseline.latest_sequence - 255
        # Reconnect discards the older retained tail as a state baseline. Nothing
        # is lost or replayed twice when resuming after this atomic cursor.
        resumed = await handle.query(
            ProofWorkflow.debug_events, DebugEventQuery(baseline.latest_sequence)
        )
        assert resumed.events == () and not resumed.gap and not resumed.has_more
        assert await handle.query(ProofWorkflow.debug_reconnect) == baseline
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
