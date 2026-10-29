"""Durable breakpoint overlays preserve gates, exact-run control and replay."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import (
    WorkflowFailureError,
    WorkflowQueryFailedError,
    WorkflowUpdateFailedError,
)
from temporalio.worker import Replayer, Worker

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    DebugBreakpointCommand,
    DebugCommand,
    ExecutionPlan,
    ReleaseBinding,
    Step,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_temporal_integration import Effects, temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def plan(*, debug=True, delay=0):
    return ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "cfg"),
        "workflow",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            Step("first", "wait", delay_seconds=delay),
            Step("second", "wait"),
            Step("effect", "activity"),
            Step("last", "wait"),
            Step("end", "end"),
        ),
        debug=debug,
        breakpoints=("second",),
    )


async def test_replacement_and_clear_preserve_current_gate_across_restart_and_replay(
    temporal_env,  # noqa: F811
    tmp_path,
):
    client = temporal_env.client
    queue, execution = "breakpoints-" + uuid4().hex, "breakpoints-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")

    async def clients(requested):
        assert requested == placement
        return client

    runtime = TemporalDebugRuntime(clients, journal)
    effects = Effects()
    frozen = plan()
    try:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            started = await client.start_workflow(
                ProofWorkflow.run,
                frozen,
                id=execution,
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            run_id = started.first_execution_run_id
            assert run_id is not None
            handle = client.get_workflow_handle(execution, run_id=run_id)
            before = await wait_state(handle, "paused", "first")
            initial = await runtime.breakpoints(placement, execution, run_id)
            assert initial.snapshot == before and initial.breakpoints == ("second",)
            command = DebugBreakpointCommand("replace", "first", ("effect",), 0)
            receipt = await runtime.replace_breakpoints(placement, execution, run_id, command)
            changed = await runtime.breakpoints(placement, execution, run_id)
            assert (
                changed.breakpoints == ("effect",)
                and changed.snapshot.revision == receipt.revision == 1
            )
            assert changed.snapshot.node_id == "first" and changed.snapshot.state == "paused"
            assert changed.snapshot.completed_nodes == ("start",) and effects.calls == []
            for rejected in (
                DebugBreakpointCommand("stale", "first", (), 0),
                DebugBreakpointCommand("wrong-node", "second", (), 1),
                DebugBreakpointCommand("invalid", "first", ("start",), 1),
                DebugBreakpointCommand("invalid-end", "first", ("end",), 1),
                DebugBreakpointCommand("unknown", "first", ("missing",), 1),
                DebugBreakpointCommand("duplicate", "first", ("effect", "effect"), 1),
                DebugBreakpointCommand("unsorted", "first", ("second", "effect"), 1),
            ):
                with pytest.raises(DebugError):
                    await runtime.replace_breakpoints(placement, execution, run_id, rejected)
            with pytest.raises(DebugError):
                await runtime.control(
                    placement, execution, run_id, DebugCommand("replace", "first", "continue", 1)
                )
            assert await runtime.breakpoints(placement, execution, run_id) == changed
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            assert (
                await runtime.replace_breakpoints(placement, execution, run_id, command) == receipt
            )
            assert await runtime.breakpoints(placement, execution, run_id) == changed
            await runtime.control(
                placement, execution, run_id, DebugCommand("go-first", "first", "continue", 1)
            )
            at_effect = await wait_state(handle, "paused", "effect")
            assert at_effect.completed_nodes == ("start", "first", "second")
            assert effects.calls == []
            # Clear the overlay; the current gate stays blocked until continue.
            cleared = await runtime.replace_breakpoints(
                placement, execution, run_id, DebugBreakpointCommand("clear", "effect", (), 2)
            )
            assert cleared.revision == 3
            assert (await runtime.breakpoints(placement, execution, run_id)).breakpoints == ()
            assert (await handle.query(ProofWorkflow.snapshot)).state == "paused"
            assert effects.calls == []
            await runtime.control(
                placement, execution, run_id, DebugCommand("finish", "effect", "continue", 3)
            )
            await asyncio.wait_for(handle.result(), timeout=20)
            assert len(effects.calls) == 1 and frozen.breakpoints == ("second",)
            assert (
                await runtime.replace_breakpoints(placement, execution, run_id, command) == receipt
            )
            assert (await runtime.breakpoints(placement, execution, run_id)).breakpoints == ()
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        journal.close()


@pytest.mark.parametrize("debug", [False, True])
async def test_running_and_normal_runs_reject_breakpoint_replacement(temporal_env, debug):  # noqa: F811
    client = temporal_env.client
    queue = "breakpoint-guard-" + uuid4().hex
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[Effects().dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            plan(debug=debug, delay=60),
            id=uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        revision = 0
        if debug:
            await wait_state(handle, "paused", "first")
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("continue", "first", "continue", 0)
            )
            revision = 1
        before = await wait_state(handle, "waiting_timer", "first")
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                ProofWorkflow.replace_breakpoints,
                DebugBreakpointCommand("replace", "first", (), revision),
            )
        assert await handle.query(ProofWorkflow.snapshot) == before
        if not debug:
            with pytest.raises(WorkflowQueryFailedError) as error:
                await handle.query(ProofWorkflow.debug_breakpoints)
            assert "Debug breakpoints are unavailable" in str(error.value)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
