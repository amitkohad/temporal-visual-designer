"""Real sandbox/replay/restart checks against an isolated local Temporal server."""

import asyncio
import json
import os
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError, CancelledError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.preflight import compile_plan
from nexusflow.modules.durable_runtime.types import (
    ActivityCall,
    ApprovalCommand,
    DebugCommand,
    ReleaseBinding,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_env():
    executable = os.environ.get("NEXUSFLOW_TEMPORAL_CLI") or shutil.which("temporal")
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=executable,
        ui=False,
    ) as env:
        yield env


class Effects:
    def __init__(self, fail_first=False):
        self.calls = []
        self.results = {}
        self.fail_first = fail_first

    @activity.defn(name="nexusflow.proof.dispatch")
    async def dispatch(self, call: ActivityCall) -> str:
        self.calls.append((call, activity.info().attempt))
        if self.fail_first and activity.info().attempt == 1:
            raise ApplicationError("Synthetic transient failure")
        if call.operation_id not in self.results:
            self.results[call.operation_id] = json.dumps({"release": call.binding.release_id})
        return self.results[call.operation_id]


def definition():
    value = json.loads((FIXTURES / "purchasing.workflow.json").read_text())
    nodes = {node["node_id"]: node for node in value["nodes"]}
    first = {**nodes["notify"], "node_id": "prepare"}
    last = {**next(node for node in nodes.values() if node["kind"] == "end"), "node_id": "end"}
    value["nodes"] = [nodes["start"], first, nodes["approval"], nodes["notify"], last]
    ids = [node["node_id"] for node in value["nodes"]]
    value["edges"] = [
        {
            "source_node_id": source,
            "source_port": "out",
            "target_node_id": target,
            "target_port": "in",
        }
        for source, target in zip(ids, ids[1:], strict=False)
    ]
    return value


def plan(release="A", debug=False):
    value = definition()
    if release == "B":
        for node in value["nodes"]:
            if node["kind"] == "activity":
                node["activity_ref"]["version"] = "2.0.0"
                node["activity_ref"]["artifact_digest"] = "sha256:" + "b" * 64
    return compile_plan(
        json.dumps(value),
        ReleaseBinding(
            "acme", "dev", release, "sha256:" + ("b" if release == "B" else "a") * 64, "config-1"
        ),
        Scope(organization_id="acme", environment_id="dev"),
        debug=debug,
        breakpoints=("notify",) if debug else (),
    )


async def wait_state(handle, state, node_id=None):
    async def observe():
        while True:
            snapshot = await handle.query(ProofWorkflow.snapshot)
            if snapshot.state == state and (node_id is None or snapshot.node_id == node_id):
                return snapshot

    return await asyncio.wait_for(observe(), timeout=20)


async def test_durable_approval_and_debug_pause_survive_worker_restart_and_replay(temporal_env):
    queue = f"runtime-proof-{uuid4()}"
    effects = Effects()
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handle = await temporal_env.client.start_workflow(
            ProofWorkflow.run,
            plan(debug=True),
            id=str(uuid4()),
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "paused", "prepare")
        step = DebugCommand("step-1", "prepare", "step_over", 0)
        assert (await handle.execute_update(ProofWorkflow.control, step)).revision == 1
        await wait_state(handle, "paused", "approval")
        await handle.execute_update(
            ProofWorkflow.control, DebugCommand("continue-1", "approval", "continue", 1)
        )
        await wait_state(handle, "waiting_approval", "approval")
        assert len(effects.results) == 1
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        waiting = await wait_state(handle, "waiting_approval")
        assert waiting.completed_nodes == ("start", "prepare")
        approval = ApprovalCommand("approval-1", "approval", True, 2)
        receipt = await handle.execute_update(ProofWorkflow.approve, approval)
        assert await handle.execute_update(ProofWorkflow.approve, approval) == receipt
        await wait_state(handle, "paused", "notify")
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        paused = await wait_state(handle, "paused", "notify")
        assert paused.revision == 3
        assert len(effects.results) == 1
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(ProofWorkflow.approve, replace(approval, approved=False))
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("stale", "notify", "continue", 0)
            )
        command = DebugCommand("continue-2", "notify", "continue", 3)
        await handle.execute_update(ProofWorkflow.control, command)
        result = await handle.result()
        assert result.completed_nodes == ("start", "prepare", "approval", "notify", "end")
        assert len(effects.results) == 2
        assert len(effects.calls) == 2
    history = await handle.fetch_history()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)


async def test_concurrent_pinned_versions_retry_identity_and_duplicate_start(temporal_env):
    queue = f"runtime-proof-{uuid4()}"
    effects = Effects(fail_first=True)
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handles = []
        for release in ("A", "B", "A"):
            pinned = plan(release)
            pinned = replace(
                pinned, steps=tuple(replace(step, maximum_attempts=2) for step in pinned.steps)
            )
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run,
                pinned,
                id=str(uuid4()),
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            handles.append(handle)
            await wait_state(handle, "waiting_approval")
        with pytest.raises(WorkflowAlreadyStartedError):
            await temporal_env.client.start_workflow(
                ProofWorkflow.run, plan("B"), id=handles[0].id, task_queue=queue
            )
        for handle in handles:
            await handle.execute_update(
                ProofWorkflow.approve, ApprovalCommand("approve", "approval", True, 0)
            )
        results = await asyncio.gather(*(handle.result() for handle in handles))
        assert [result.binding.release_id for result in results] == ["A", "B", "A"]
        assert results[0].definition_digest != results[1].definition_digest
        assert results[0].definition_digest == results[2].definition_digest
        assert len(effects.results) == 6
        for call, _ in effects.calls:
            expected = "2.0.0" if call.binding.release_id == "B" else "1.0.0"
            assert call.activity_version == expected
        for operation_id in effects.results:
            attempts = [
                attempt for call, attempt in effects.calls if call.operation_id == operation_id
            ]
            assert attempts == [1, 2]
        for handle in handles:
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_durable_timer_survives_worker_restart(temporal_env):
    value = definition()
    approval = next(node for node in value["nodes"] if node["kind"] == "approval")
    approval["kind"] = "wait"
    approval["input_mapping"] = {"seconds": 1}
    pinned = compile_plan(
        json.dumps(value), plan().binding, Scope(organization_id="acme", environment_id="dev")
    )
    effects = Effects()
    queue = f"runtime-proof-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handle = await temporal_env.client.start_workflow(
            ProofWorkflow.run,
            pinned,
            id=str(uuid4()),
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "waiting_timer")
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        result = await handle.result()
        assert result.completed_nodes == ("start", "prepare", "approval", "notify", "end")
        assert len(effects.calls) == 2
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_rejected_approval_does_not_dispatch_later_effects(temporal_env):
    effects = Effects()
    queue = f"runtime-proof-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handle = await temporal_env.client.start_workflow(
            ProofWorkflow.run,
            plan(),
            id=str(uuid4()),
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "waiting_approval")
        await handle.execute_update(
            ProofWorkflow.approve, ApprovalCommand("reject", "approval", False, 0)
        )
        with pytest.raises(WorkflowFailureError) as error:
            await handle.result()
        assert isinstance(error.value.cause, ApplicationError)
        assert error.value.cause.type == "ApprovalRejected"
        assert len(effects.calls) == 1
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_cancelling_paused_debug_run_dispatches_nothing(temporal_env):
    effects = Effects()
    queue = f"runtime-proof-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handle = await temporal_env.client.start_workflow(
            ProofWorkflow.run,
            plan(debug=True),
            id=str(uuid4()),
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        await wait_state(handle, "paused", "prepare")
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as error:
            await handle.result()
        assert isinstance(error.value.cause, CancelledError)
        assert effects.calls == []
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


async def test_retry_exhaustion_does_not_enter_approval(temporal_env):
    effects = Effects(fail_first=True)
    queue = f"runtime-proof-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[effects.dispatch],
    ):
        handle = await temporal_env.client.start_workflow(
            ProofWorkflow.run,
            plan(),
            id=str(uuid4()),
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        assert len(effects.calls) == 1
        assert effects.results == {}
        snapshot = await handle.query(ProofWorkflow.snapshot)
        assert snapshot.state == "failed"
        assert snapshot.completed_nodes == ("start",)
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
