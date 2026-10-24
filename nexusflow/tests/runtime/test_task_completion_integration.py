"""Real Temporal acceptance for durable human-task completion delivery."""

from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.service import RPCError
from temporalio.worker import Replayer, Worker

from nexusflow.modules.durable_runtime.human_completions import (
    CompletionTarget,
    SQLiteCompletionJournal,
    TemporalCompletionSink,
)
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding, Step
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask, DecisionRequest, TaskError
from nexusflow.tests.human_tasks.test_acceptance import SCOPE, Authorization, Directory
from nexusflow.tests.runtime.test_temporal_integration import temporal_env as temporal_env
from nexusflow.tests.runtime.test_temporal_integration import wait_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def execution_plan(two_approvals=False):
    steps = [Step("start", "start"), Step("approval", "approval")]
    if two_approvals:
        steps.append(Step("second", "approval"))
    steps.append(Step("end", "end"))
    return ExecutionPlan(
        ReleaseBinding("org", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        "onboarding",
        "sha256:" + "b" * 64,
        tuple(steps),
    )


def create_completion(service, handle, node="approval", decision="approve"):
    task = service.create(
        SCOPE,
        CreateTask(
            run_id=handle.id,
            node_invocation_id=node,
            definition_revision="release-1",
            rule="any_one",
            user_ids=("alice",),
        ),
    )
    service.decide(
        SCOPE,
        task.task_id,
        DecisionRequest(command_id="decision", expected_revision=1, decision=decision),
    )
    return next(
        value for value in service.pending_completions(SCOPE) if value.task_id == task.task_id
    )


def register(journal, completion, handle, **overrides):
    values = dict(
        scope=SCOPE,
        task_id=completion.task_id,
        run_id=completion.run_id,
        node_invocation_id=completion.node_invocation_id,
        definition_revision=completion.definition_revision,
        workflow_id=handle.id,
        temporal_run_id=handle.result_run_id,
        definition_digest=execution_plan().definition_digest,
    )
    values.update(overrides)
    journal.register(CompletionTarget(**values))


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("loss", ["journal", "outbox"])
async def test_real_task_completion_ack_survives_terminal_workflow_and_journal_reopen(
    temporal_env, tmp_path, decision, loss
):
    service = TaskService(tmp_path / "tasks.db", Authorization(), Directory(("alice",)))

    class LostJournalAcknowledgement(SQLiteCompletionJournal):
        def acknowledge(self, target, command, receipt):
            if loss == "journal":
                raise OSError("Receipt persistence failed after accepted Temporal update")
            super().acknowledge(target, command, receipt)

    journal = LostJournalAcknowledgement(tmp_path / "journal.db")
    queue = f"completion-{uuid4()}"
    try:
        async with Worker(temporal_env.client, task_queue=queue, workflows=[ProofWorkflow]):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run, execution_plan(), id=str(uuid4()), task_queue=queue
            )
            await wait_state(handle, "waiting_approval")
            completion = create_completion(service, handle, decision=decision)
            register(journal, completion, handle)
            sink = TemporalCompletionSink(temporal_env.client, journal, SCOPE)

            class LostAcknowledgement:
                async def deliver(self, value):
                    await sink.deliver(value)
                    raise OSError("Lost local acknowledgement after durable destination receipt")

            with pytest.raises(TaskError) as failure:
                await service.deliver_pending_async(SCOPE, LostAcknowledgement())
            assert failure.value.code == "delivery_unavailable"
            assert service.pending_completions(SCOPE) == (completion,)
            if decision == "approve":
                assert (await handle.result()).completed_nodes == ("start", "approval", "end")
            else:
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                assert (await handle.query(ProofWorkflow.snapshot)).state == "failed"
            journal.close()
            service.close()
            journal = SQLiteCompletionJournal(tmp_path / "journal.db")
            service = TaskService(tmp_path / "tasks.db", Authorization(), Directory(("alice",)))
            sink = TemporalCompletionSink(temporal_env.client, journal, SCOPE)
            assert await service.deliver_pending_async(SCOPE, sink) == 1
            assert service.pending_completions(SCOPE) == ()
            assert await service.deliver_pending_async(SCOPE, sink) == 0
            await sink.deliver(completion)
            snapshot = await handle.query(ProofWorkflow.snapshot)
            assert snapshot.revision == 1
    finally:
        service.close()
        journal.close()


async def test_two_approval_nodes_do_not_cross_complete_and_survive_worker_restart(
    temporal_env, tmp_path
):
    service = TaskService(tmp_path / "tasks.db", Authorization(), Directory(("alice",)))
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    queue = f"completion-{uuid4()}"
    try:
        sink = TemporalCompletionSink(temporal_env.client, journal, SCOPE)
        async with Worker(temporal_env.client, task_queue=queue, workflows=[ProofWorkflow]):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run,
                execution_plan(two_approvals=True),
                id=str(uuid4()),
                task_queue=queue,
                execution_timeout=timedelta(minutes=2),
            )
            await wait_state(handle, "waiting_approval", "approval")
            first = create_completion(service, handle)
            second = create_completion(service, handle, "second")
            register(journal, first, handle)
            register(journal, second, handle)
            with pytest.raises((WorkflowUpdateFailedError, RPCError)):
                await sink.deliver(second)
            unchanged = await handle.query(ProofWorkflow.snapshot)
            assert unchanged.node_id == "approval" and unchanged.revision == 0
            await sink.deliver(first)
            await wait_state(handle, "waiting_approval", "second")
        async with Worker(temporal_env.client, task_queue=queue, workflows=[ProofWorkflow]):
            await wait_state(handle, "waiting_approval", "second")
            await sink.deliver(first)
            assert (await handle.query(ProofWorkflow.snapshot)).revision == 1
            assert await service.deliver_pending_async(SCOPE, sink) == 2
            assert (await handle.result()).completed_nodes == ("start", "approval", "second", "end")
            assert (await handle.query(ProofWorkflow.snapshot)).revision == 2
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()


@pytest.mark.parametrize("wrong", ["scope", "digest", "run"])
async def test_invalid_target_binding_leaves_workflow_waiting_and_outbox_pending(
    temporal_env, tmp_path, wrong
):
    service = TaskService(tmp_path / "tasks.db", Authorization(), Directory(("alice",)))
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    queue = f"completion-{uuid4()}"
    try:
        async with Worker(temporal_env.client, task_queue=queue, workflows=[ProofWorkflow]):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run, execution_plan(), id=str(uuid4()), task_queue=queue
            )
            await wait_state(handle, "waiting_approval")
            completion = create_completion(service, handle)
            override = {
                "scope": {"scope": SCOPE.model_copy(update={"environment_id": "uat"})},
                "digest": {"definition_digest": "sha256:" + "c" * 64},
                "run": {"temporal_run_id": str(uuid4())},
            }[wrong]
            register(journal, completion, handle, **override)
            sink = TemporalCompletionSink(
                temporal_env.client, journal, SCOPE, rpc_timeout=timedelta(seconds=2)
            )
            with pytest.raises(TaskError):
                await service.deliver_pending_async(SCOPE, sink)
            snapshot = await handle.query(ProofWorkflow.snapshot)
            assert snapshot.state == "waiting_approval" and snapshot.revision == 0
            assert service.pending_completions(SCOPE) == (completion,)
            await handle.cancel()
    finally:
        service.close()
        journal.close()
