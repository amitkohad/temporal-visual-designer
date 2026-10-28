"""Selected completed activity outputs survive worker replacement and replay."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import WorkflowFailureError
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


def output_plan(debug=True):
    return ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "cfg"),
        "flow",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            Step("effect", "activity"),
            Step("later", "wait"),
            Step("end", "end"),
        ),
        debug=debug,
    )


async def test_completed_output_is_read_only_pinned_and_replayed(temporal_env, tmp_path):  # noqa: F811
    client = temporal_env.client
    queue = "output-" + uuid4().hex
    execution = "output-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    effects = Effects()
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")

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
                output_plan(),
                id=execution,
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            run_id = started.first_execution_run_id
            assert run_id is not None
            handle = client.get_workflow_handle(execution, run_id=run_id)
            initial = await wait_state(handle, "paused", "effect")
            for node in ("effect", "start", "later", "unknown"):
                with pytest.raises(DebugError) as error:
                    await runtime.step_output(placement, execution, run_id, node)
                assert error.value.code == "conflict"
            assert await handle.query(ProofWorkflow.snapshot) == initial
            await runtime.control(
                placement, execution, run_id, DebugCommand("step", "effect", "step_over", 0)
            )
            paused = await wait_state(handle, "paused", "later")
            value = await runtime.step_output(placement, execution, run_id, "effect")
            assert value.snapshot == paused and value.node_id == "effect"
            assert value.output_json == '{"release": "release"}'
            assert await handle.query(ProofWorkflow.snapshot) == paused
            assert len(effects.calls) == 1
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[effects.dispatch]
        ):
            assert await runtime.step_output(placement, execution, run_id, "effect") == value
            assert len(effects.calls) == 1
            await runtime.control(
                placement, execution, run_id, DebugCommand("finish", "later", "continue", 1)
            )
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert result["activity_outputs"] == {"effect": value.output_json}
            completed = await runtime.step_output(placement, execution, run_id, "effect")
            assert (
                completed.snapshot.state == "completed"
                and completed.output_json == value.output_json
            )
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        journal.close()


async def test_normal_execution_rejects_output_query_without_advancing_wait(temporal_env, tmp_path):  # noqa: F811
    client = temporal_env.client
    queue = "normal-output-" + uuid4().hex
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), client.namespace, queue
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")

    async def clients(requested):
        return client

    runtime = TemporalDebugRuntime(clients, journal)
    plan = output_plan(debug=False)
    # A long wait keeps the normal run open after its completed activity.
    plan = ExecutionPlan(
        plan.binding,
        plan.workflow_id,
        plan.definition_digest,
        (*plan.steps[:2], Step("later", "wait", delay_seconds=60), plan.steps[-1]),
    )
    try:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[Effects().dispatch]
        ):
            handle = await client.start_workflow(
                ProofWorkflow.run, plan, id=uuid4().hex, task_queue=queue
            )
            waiting = await wait_state(handle, "waiting_timer", "later")
            run_id = handle.first_execution_run_id
            assert run_id is not None
            with pytest.raises(DebugError) as error:
                await runtime.step_output(placement, handle.id, run_id, "effect")
            assert error.value.code == "conflict"
            assert await handle.query(ProofWorkflow.snapshot) == waiting
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        journal.close()
