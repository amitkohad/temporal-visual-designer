"""Background delivery acceptance against SQLite and a real Temporal server."""

import asyncio
from itertools import count
from uuid import UUID, uuid4

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.worker import Replayer, Worker

from nexusflow.modules.durable_runtime.human_completions import (
    SQLiteCompletionJournal,
    TemporalCompletionSink,
)
from nexusflow.modules.durable_runtime.task_creation import TaskCreationActivities
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.dispatch import CompletionDispatcher, run_completion_dispatcher
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask, DecisionRequest
from nexusflow.tests.human_tasks.test_acceptance import OTHER, SCOPE, Authorization
from nexusflow.tests.runtime.test_managed_tasks_integration import RequestedDirectory, managed_plan
from nexusflow.tests.runtime.test_temporal_integration import temporal_env as temporal_env
from nexusflow.tests.runtime.test_temporal_integration import wait_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def vote(service, task, decision="approve"):
    return service.decide(
        SCOPE,
        task.task_id,
        DecisionRequest(command_id="vote", expected_revision=1, decision=decision),
    )


async def wait_empty(service):
    async with asyncio.timeout(10):
        while service.pending_completions(SCOPE):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_background_dispatch_resumes_managed_workflow(temporal_env, tmp_path, decision):
    service = TaskService(tmp_path / "tasks.db", Authorization(), RequestedDirectory())
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    activities = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)
    queue = f"background-{uuid4()}"
    dispatcher = CompletionDispatcher(
        service,
        SCOPE,
        TemporalCompletionSink(temporal_env.client, journal, SCOPE),
        poll_interval=0.01,
    )
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            async with run_completion_dispatcher(dispatcher):
                handle = await temporal_env.client.start_workflow(
                    ProofWorkflow.run,
                    managed_plan({"rule": "any_one", "user_ids": ["alice"]}),
                    id=str(uuid4()),
                    task_queue=queue,
                )
                await wait_state(handle, "waiting_approval")
                (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
                vote(service, task, decision)
                if decision == "approve":
                    assert (await asyncio.wait_for(handle.result(), 10)).completed_nodes == (
                        "start",
                        "approval",
                        "end",
                    )
                else:
                    with pytest.raises(WorkflowFailureError):
                        await asyncio.wait_for(handle.result(), 10)
                await wait_empty(service)
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()


async def test_missing_destination_cannot_starve_managed_completion_or_cross_scope(
    temporal_env, tmp_path, monkeypatch
):
    service = TaskService(tmp_path / "tasks.db", Authorization(), RequestedDirectory())
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    activities = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)
    queue = f"fair-background-{uuid4()}"
    try:
        # Manual UUID task IDs sort before the managed bound-* ID. Fix only
        # these IDs so page size one must encounter a poison row first.
        ids = (UUID(int=index) for index in count(1))
        with monkeypatch.context() as patches:
            patches.setattr("nexusflow.modules.human_tasks.service.uuid4", lambda: next(ids))
            poison = [
                service.create(
                    SCOPE,
                    CreateTask(
                        run_id=f"unregistered-{index}",
                        node_invocation_id="approval",
                        definition_revision="release-1",
                        rule="any_one",
                        user_ids=("alice",),
                    ),
                )
                for index in range(3)
            ]
        for task in poison:
            vote(service, task)
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run,
                managed_plan({"rule": "any_one", "user_ids": ["alice"]}),
                id=str(uuid4()),
                task_queue=queue,
            )
            await wait_state(handle, "waiting_approval")
            (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
            vote(service, task)
            wrong_scope = CompletionDispatcher(
                service,
                OTHER,
                TemporalCompletionSink(temporal_env.client, journal, OTHER),
                poll_interval=0.01,
            )
            async with run_completion_dispatcher(wrong_scope):
                await asyncio.sleep(0.05)
                assert (await handle.query(ProofWorkflow.snapshot)).revision == 0
                assert len(service.pending_completions(SCOPE)) == 4
            dispatcher = CompletionDispatcher(
                service,
                SCOPE,
                TemporalCompletionSink(temporal_env.client, journal, SCOPE),
                batch_size=1,
                poll_interval=0.01,
                delivery_timeout=0.5,
            )
            async with run_completion_dispatcher(dispatcher):
                assert (await asyncio.wait_for(handle.result(), 10)).completed_nodes[-1] == "end"
                async with asyncio.timeout(10):
                    while len(service.pending_completions(SCOPE)) != 3:
                        await asyncio.sleep(0.01)
            assert {item.task_id for item in service.pending_completions(SCOPE)} == {
                item.task_id for item in poison
            }
    finally:
        service.close()
        journal.close()


async def test_cancelled_lost_ack_restarts_from_persisted_receipt(temporal_env, tmp_path):
    tasks_path, journal_path = tmp_path / "tasks.db", tmp_path / "journal.db"
    service = TaskService(tasks_path, Authorization(), RequestedDirectory())
    journal = SQLiteCompletionJournal(journal_path)
    activities = TaskCreationActivities(service, journal, temporal_env.client, SCOPE)
    base = TemporalCompletionSink(temporal_env.client, journal, SCOPE)
    accepted, cancelled = asyncio.Event(), asyncio.Event()

    class LostAcknowledgement:
        async def deliver(self, completion):
            await base.deliver(completion)
            accepted.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    queue = f"restart-background-{uuid4()}"
    try:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ProofWorkflow],
            activities=[activities.create],
        ):
            handle = await temporal_env.client.start_workflow(
                ProofWorkflow.run,
                managed_plan({"rule": "any_one", "user_ids": ["alice"]}),
                id=str(uuid4()),
                task_queue=queue,
            )
            await wait_state(handle, "waiting_approval")
            (task,) = service.list_tasks(SCOPE, run_id=handle.id).items
            vote(service, task)
            dispatcher = CompletionDispatcher(
                service, SCOPE, LostAcknowledgement(), poll_interval=0.01, delivery_timeout=30
            )
            async with asyncio.timeout(10):
                async with run_completion_dispatcher(dispatcher):
                    await accepted.wait()
                    assert (await handle.result()).completed_nodes[-1] == "end"
            assert cancelled.is_set()
            assert len(service.pending_completions(SCOPE)) == 1
            snapshot = await handle.query(ProofWorkflow.snapshot)
            assert snapshot.revision == 1
        service.close()
        journal.close()
        service = TaskService(tasks_path, Authorization(), RequestedDirectory())
        journal = SQLiteCompletionJournal(journal_path)
        # The workflow is closed and its execution worker stopped. The permanent
        # receipt must still safely clear the outbox after both stores reopen.
        dispatcher = CompletionDispatcher(
            service,
            SCOPE,
            TemporalCompletionSink(temporal_env.client, journal, SCOPE),
            poll_interval=0.01,
        )
        async with run_completion_dispatcher(dispatcher):
            await wait_empty(service)
        assert (await handle.result()).completed_nodes == ("start", "approval", "end")
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        service.close()
        journal.close()
