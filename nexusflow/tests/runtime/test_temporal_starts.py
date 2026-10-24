"""Dispatch journal security, ambiguity and permanent receipt acceptance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from temporalio.api.common.v1 import Payloads, WorkflowType
from temporalio.api.history.v1 import HistoryEvent, WorkflowExecutionStartedEventAttributes
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.converter import DataConverter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.starts import AsyncStartService, StartError
from nexusflow.modules.durable_runtime.temporal_starts import (
    SQLiteDispatchJournal,
    TemporalStartDispatcher,
)
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding, Step


@pytest.fixture
def setup(tmp_path):
    client = SimpleNamespace(
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="localhost:7233")),
        namespace="default",
        data_converter=DataConverter.default,
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id=None, result_run_id="run-1")),
        get_workflow_handle=Mock(),
    )
    journal = SQLiteDispatchJournal(tmp_path / "dispatch.db")
    scope = Scope(organization_id="acme", environment_id="dev")
    dispatcher = TemporalStartDispatcher(client, journal, scope, "queue")
    plan = ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "cfg"),
        "flow",
        "sha256:" + "b" * 64,
        (Step("start", "start"), Step("end", "end")),
    )
    yield dispatcher, "nf-" + uuid4().hex, plan, tmp_path / "dispatch.db"
    journal.close()


async def history(client, plan, *, workflow_type="NexusFlowProofV1", queue="queue"):
    payloads = await client.data_converter.encode([plan])
    event = HistoryEvent(
        workflow_execution_started_event_attributes=WorkflowExecutionStartedEventAttributes(
            workflow_type=WorkflowType(name=workflow_type),
            task_queue=TaskQueue(name=queue),
            input=Payloads(payloads=payloads),
        )
    )

    async def events(**kwargs):
        yield event

    client.get_workflow_handle.return_value = SimpleNamespace(
        describe=AsyncMock(return_value=SimpleNamespace(run_id="run-existing")),
        fetch_history_events=events,
    )


@pytest.mark.asyncio
async def test_successful_receipt_survives_restart_and_remote_history_loss(setup):
    dispatcher, execution, plan, database = setup
    await dispatcher.start(execution, plan)
    kwargs = dispatcher.client.start_workflow.call_args.kwargs
    assert kwargs["id"] == execution
    assert kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert kwargs["id_conflict_policy"] == WorkflowIDConflictPolicy.FAIL
    reopened = SQLiteDispatchJournal(database)
    try:
        restored = TemporalStartDispatcher(dispatcher.client, reopened, dispatcher.scope, "queue")
        dispatcher.client.get_workflow_handle.side_effect = AssertionError("History expired")
        await restored.start(execution, plan)
        assert dispatcher.client.start_workflow.await_count == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_ambiguous_delivery_reconciles_exact_plan_without_another_start(setup):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = TimeoutError()
    with pytest.raises(OSError):
        await dispatcher.start(execution, plan)
    await history(dispatcher.client, plan)
    await dispatcher.start(execution, plan)
    await dispatcher.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_missing_history_remains_pending_without_restarting(setup):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = TimeoutError()
    with pytest.raises(OSError):
        await dispatcher.start(execution, plan)
    handle = SimpleNamespace(
        describe=AsyncMock(side_effect=RPCError("missing", RPCStatusCode.NOT_FOUND, b""))
    )
    dispatcher.client.get_workflow_handle.return_value = handle
    for _ in range(2):
        with pytest.raises(OSError):
            await dispatcher.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"workflow_type": "other"}, {"queue": "other"}, {"different_plan": True}]
)
async def test_preexisting_workflow_must_match_type_queue_and_complete_plan(setup, changes):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        execution, "NexusFlowProofV1"
    )
    existing = (
        replace(plan, input_json='{"changed":true}')
        if changes.pop("different_plan", False)
        else plan
    )
    await history(dispatcher.client, existing, **changes)
    with pytest.raises(StartError) as error:
        await dispatcher.start(execution, plan)
    assert error.value.code == "conflict"


@pytest.mark.asyncio
async def test_preexisting_matching_execution_is_acknowledged(setup):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        execution, "NexusFlowProofV1"
    )
    await history(dispatcher.client, plan)
    await dispatcher.start(execution, plan)
    await dispatcher.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_receipt_rejects_changed_plan_or_destination(setup):
    dispatcher, execution, plan, _ = setup
    await dispatcher.start(execution, plan)
    for changed in [
        replace(plan, input_json='{"changed":true}'),
        replace(plan, binding=replace(plan.binding, configuration_revision="new")),
    ]:
        with pytest.raises(StartError) as error:
            await dispatcher.start(execution, changed)
        assert error.value.code == "conflict"
    other = TemporalStartDispatcher(
        dispatcher.client, dispatcher.journal, dispatcher.scope, "another-queue"
    )
    with pytest.raises(StartError):
        await other.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_scope_denial_and_unsupported_runtime_have_zero_effects(setup):
    dispatcher, execution, plan, _ = setup
    with pytest.raises(PermissionError):
        await dispatcher.start(
            execution, replace(plan, binding=replace(plan.binding, organization_id="other"))
        )
    with pytest.raises(StartError):
        await dispatcher.start("arbitrary-id", plan)
    with pytest.raises(StartError):
        await dispatcher.start(
            execution, replace(plan, binding=replace(plan.binding, runtime_version="future"))
        )
    dispatcher.client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_service_reauthorizes_and_maps_pending_errors(tmp_path):
    from nexusflow.modules.durable_runtime.starts import SQLiteStartRepository, StartRequest
    from nexusflow.tests.runtime.test_starts import Authorization, Releases

    repository = SQLiteStartRepository(tmp_path / "starts.db")
    authorization, releases = Authorization(), Releases()
    dispatcher = SimpleNamespace(start=AsyncMock(side_effect=OSError("unavailable")))
    service = AsyncStartService(repository, authorization, releases, dispatcher)
    request = StartRequest(
        Scope(organization_id="acme", environment_id="dev"), "hr", "purchase-requisition", "key"
    )
    try:
        with pytest.raises(StartError) as error:
            await service.start(request)
        assert error.value.code == "unavailable"
        dispatcher.start.side_effect = None
        first = await service.start(request)
        assert await service.start(request) == first
        assert dispatcher.start.await_count == 2
        authorization.denied = True
        with pytest.raises(PermissionError):
            await service.start(request)
    finally:
        repository.close()


def test_separate_connections_grant_only_one_dispatch_claim(setup):
    dispatcher, execution, _, database = setup
    other = SQLiteDispatchJournal(database)
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda journal: journal.claim(execution, "route", "digest"),
                    [dispatcher.journal, other],
                )
            )
        assert sorted(results) == [(False, None), (True, None)]
        dispatcher.journal.acknowledge(execution, "run-1")
        assert other.claim(execution, "route", "digest") == (False, "run-1")
        with pytest.raises(StartError):
            other.acknowledge(execution, "run-2")
    finally:
        other.close()


@pytest.mark.asyncio
async def test_cancellation_preserves_claim_and_retry_only_reconciles(setup):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.start(execution, plan)
    await history(dispatcher.client, plan)
    await dispatcher.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [None, HistoryEvent()])
async def test_missing_or_invalid_start_event_is_not_acknowledged(setup, event):
    dispatcher, execution, plan, _ = setup
    dispatcher.client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        execution, "NexusFlowProofV1"
    )

    async def events(**kwargs):
        if event is not None:
            yield event

    dispatcher.client.get_workflow_handle.return_value = SimpleNamespace(
        describe=AsyncMock(return_value=SimpleNamespace(run_id="run-1")),
        fetch_history_events=events,
    )
    with pytest.raises(StartError) as error:
        await dispatcher.start(execution, plan)
    assert error.value.code == "conflict"
    # The failed verification leaves no successful receipt: retry verifies again.
    with pytest.raises(StartError):
        await dispatcher.start(execution, plan)
    assert dispatcher.client.start_workflow.await_count == 1
