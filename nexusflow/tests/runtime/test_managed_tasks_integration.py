"""Real Temporal acceptance for automatic SQLite approval task creation."""

import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from nexusflow.modules.durable_runtime.human_completions import (
    SQLiteCompletionJournal,
    TemporalCompletionSink,
)
from nexusflow.modules.durable_runtime.task_creation import TaskCreationActivities
from nexusflow.modules.durable_runtime.types import (
    ApprovalCommand,
    ExecutionPlan,
    ReleaseBinding,
    Step,
    TaskCompletionCommand,
    TaskCreationCall,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import DecisionRequest
from nexusflow.tests.human_tasks.test_acceptance import SCOPE, Authorization
from nexusflow.tests.runtime.test_temporal_integration import temporal_env as temporal_env
from nexusflow.tests.runtime.test_temporal_integration import wait_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class RequestedDirectory:
    def resolve(self, scope, user_ids, group_ids):
        assert scope == SCOPE and not group_ids
        return user_ids


def managed_plan(approval):
    return ExecutionPlan(
        ReleaseBinding("org", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        "onboarding",
        "sha256:" + "b" * 64,
        (
            Step("start", "start"),
            Step("approval", "approval", task_approval_json=json.dumps(approval)),
            Step("end", "end"),
        ),
    )


async def test_pre_upgrade_step_payload_without_managed_field_retains_manual_approval(temporal_env):
    payload = asdict(managed_plan({"rule": "any_one", "user_ids": ["alice"]}))
    for step in payload["steps"]:
        del step["task_approval_json"]
    queue = f"legacy-manual-{uuid4()}"
    async with Worker(temporal_env.client, task_queue=queue, workflows=[ProofWorkflow]):
        handle = await temporal_env.client.start_workflow(
            "NexusFlowProofV1", payload, id=str(uuid4()), task_queue=queue
        )
        await wait_state(handle, "waiting_approval")
        receipt = await handle.execute_update(
            ProofWorkflow.approve, ApprovalCommand("legacy-approval", "approval", True, 0)
        )
        assert receipt.revision == 1
        result = await handle.result()
        assert result["completed_nodes"] == ["start", "approval", "end"]
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_managed_task_creates_inbox_and_exact_binding_then_resumes_or_rejects(
    temporal_env, tmp_path, decision
):
    authorization = Authorization()
    service = TaskService(tmp_path / "tasks.db", authorization, RequestedDirectory())
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    activities = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)
    queue = f"managed-{uuid4()}"
    plan = managed_plan({"rule": "any_one", "user_ids": ["alice"]})
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run, plan, id=str(uuid4()), task_queue=queue
            )
            await wait_state(handle, "waiting_approval")
            (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
            assert task.scope == SCOPE
            assert task.request.run_id == handle.id
            assert task.request.node_invocation_id == "approval"
            assert task.request.definition_revision == "release-1"
            target = journal.target(SCOPE, task.task_id)
            assert target.workflow_id == handle.id
            assert target.temporal_run_id == handle.result_run_id
            assert target.definition_digest == plan.definition_digest
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(
                    ProofWorkflow.approve, ApprovalCommand("bypass", "approval", True, 0)
                )
            wrong = TaskCompletionCommand(
                "forged",
                "different-task",
                "org",
                "dev",
                handle.id,
                handle.result_run_id,
                "approval",
                plan.definition_digest,
                "release-1",
                "approved",
                2,
            )
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(ProofWorkflow.complete_task, wrong)
            assert (await handle.query(ProofWorkflow.snapshot)).revision == 0
            service.decide(
                SCOPE,
                task.task_id,
                DecisionRequest(command_id="vote", expected_revision=1, decision=decision),
            )
            assert (
                await service.deliver_pending_async(
                    SCOPE, TemporalCompletionSink(temporal_env.client, journal, SCOPE)
                )
                == 1
            )
            if decision == "approve":
                assert (await handle.result()).completed_nodes == ("start", "approval", "end")
            else:
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
            assert service.pending_completions(SCOPE) == ()
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()


@pytest.mark.parametrize("rule", ["all_approvers", "sequential"])
async def test_managed_multivote_task_survives_worker_restart(temporal_env, tmp_path, rule):
    authorization = Authorization()
    service = TaskService(tmp_path / "tasks.db", authorization, RequestedDirectory())
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    activities = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)
    approval = {"rule": rule, "user_ids": ["alice", "bob"]}
    if rule == "sequential":
        approval = {
            "rule": rule,
            "stages": [
                {"stage_id": "first", "rule": "any_one", "user_ids": ["alice"]},
                {"stage_id": "second", "rule": "any_one", "user_ids": ["bob"]},
            ],
        }
    queue = f"managed-stages-{uuid4()}"
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run, managed_plan(approval), id=str(uuid4()), task_queue=queue
            )
            await wait_state(handle, "waiting_approval")
            (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
            updated = service.decide(
                SCOPE,
                task.task_id,
                DecisionRequest(command_id="alice-vote", expected_revision=1, decision="approve"),
            )
            assert updated.status == "pending"
            assert service.pending_completions(SCOPE) == ()
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            await wait_state(handle, "waiting_approval")
            authorization.actor = "bob"
            service.decide(
                SCOPE,
                task.task_id,
                DecisionRequest(
                    command_id="bob-vote", expected_revision=updated.revision, decision="approve"
                ),
            )
            assert (
                await service.deliver_pending_async(
                    SCOPE, TemporalCompletionSink(temporal_env.client, journal, SCOPE)
                )
                == 1
            )
            assert (await handle.result()).completed_nodes == ("start", "approval", "end")
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()


async def test_creation_activity_retries_lost_result_without_duplicate_task(temporal_env, tmp_path):
    service = TaskService(tmp_path / "tasks.db", Authorization(), RequestedDirectory())
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    base = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)

    class LostResult:
        def __init__(self):
            self.ids = []

        @activity.defn(name="nexusflow.tasks.create")
        async def create(self, call: TaskCreationCall) -> str:
            task_id = await base.create(call)
            self.ids.append(task_id)
            if activity.info().attempt == 1:
                raise ApplicationError("Synthetic loss after committed task and destination")
            return task_id

    lost = LostResult()
    queue = f"managed-retry-{uuid4()}"
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[lost.create],
        ):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run,
                managed_plan({"rule": "any_one", "user_ids": ["alice"]}),
                id=str(uuid4()),
                task_queue=queue,
            )
            await wait_state(handle, "waiting_approval")
            (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
            assert lost.ids == [task.task_id, task.task_id]
            assert [event.kind for event in service.audit(SCOPE, task.task_id)] == ["created"]
            target = journal.target(SCOPE, task.task_id)
            assert target.temporal_run_id == handle.result_run_id
            service.decide(
                SCOPE,
                task.task_id,
                DecisionRequest(command_id="approve", expected_revision=1, decision="approve"),
            )
            assert (
                await service.deliver_pending_async(
                    SCOPE, TemporalCompletionSink(temporal_env.client, journal, SCOPE)
                )
                == 1
            )
            assert (await handle.result()).completed_nodes == ("start", "approval", "end")
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()
