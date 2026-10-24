"""Immutable destination, permanent receipt and uncertain update acceptance."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.durable_runtime.human_completions import (
    CompletionTarget,
    SQLiteCompletionJournal,
    TemporalCompletionSink,
)
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    ExecutionPlan,
    ReleaseBinding,
    Step,
    TaskCompletionCommand,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.types import Completion, TaskError


@pytest.fixture
def setup(tmp_path):
    scope = Scope(organization_id="acme", environment_id="dev")
    target = CompletionTarget(
        scope=scope,
        task_id="task",
        run_id="execution",
        node_invocation_id="approve",
        definition_revision="release",
        workflow_id="execution",
        temporal_run_id="exact-run",
        definition_digest="sha256:" + "a" * 64,
    )
    completion = Completion(
        completion_id="completion",
        task_id="task",
        scope=scope,
        run_id="execution",
        node_invocation_id="approve",
        definition_revision="release",
        outcome="approved",
        task_revision=2,
    )
    handle = SimpleNamespace(
        execute_update=AsyncMock(return_value=CommandReceipt("completion", 1)),
        get_update_handle=Mock(
            return_value=SimpleNamespace(
                result=AsyncMock(return_value=CommandReceipt("completion", 1))
            )
        ),
    )
    client = SimpleNamespace(
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="localhost:7233")),
        namespace="acme-dev",
        get_workflow_handle=Mock(return_value=handle),
    )
    path = tmp_path / "completions.db"
    journal = SQLiteCompletionJournal(path)
    journal.register(target)
    yield client, journal, target, completion, path
    journal.close()


def test_strict_target_and_immutable_registration(setup):
    _, journal, target, _, _ = setup
    journal.register(target)
    with pytest.raises(ValidationError):
        CompletionTarget.model_validate({**target.model_dump(), "forged": True})
    for update in ({"temporal_run_id": "other"}, {"definition_revision": "other"}):
        with pytest.raises(TaskError, match="another completion destination"):
            journal.register(CompletionTarget.model_validate({**target.model_dump(), **update}))
    with pytest.raises(TaskError, match="execution must match"):
        journal.register(target.model_copy(update={"run_id": "other"}))


@pytest.mark.asyncio
async def test_exact_route_and_permanent_receipt_survive_reopen(setup):
    client, journal, target, completion, path = setup
    sink = TemporalCompletionSink(client, journal, target.scope)
    await sink.deliver(completion)
    client.get_workflow_handle.assert_called_once_with("execution", run_id="exact-run")
    handle = client.get_workflow_handle.return_value
    command = handle.execute_update.call_args.args[1]
    assert command.definition_revision == "release"
    assert command.temporal_run_id == "exact-run"
    journal.close()
    reopened = SQLiteCompletionJournal(path)
    try:
        await TemporalCompletionSink(client, reopened, target.scope).deliver(completion)
        assert handle.execute_update.await_count == 1
        client.namespace = "other"
        with pytest.raises(TaskError, match="destination changed"):
            await TemporalCompletionSink(client, reopened, target.scope).deliver(completion)
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other"),
        ("node_invocation_id", "other"),
        ("definition_revision", "other"),
    ],
)
async def test_wrong_binding_never_dispatches(setup, field, value):
    client, journal, target, completion, _ = setup
    with pytest.raises(TaskError, match="registered invocation"):
        await TemporalCompletionSink(client, journal, target.scope).deliver(
            completion.model_copy(update={field: value})
        )
    client.get_workflow_handle.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_scope_and_changed_payload_fail_closed(setup):
    client, journal, target, completion, _ = setup
    sink = TemporalCompletionSink(client, journal, target.scope)
    with pytest.raises(PermissionError):
        await sink.deliver(
            completion.model_copy(
                update={"scope": Scope(organization_id="other", environment_id="dev")}
            )
        )
    await sink.deliver(completion)
    with pytest.raises(TaskError, match="content or destination changed"):
        await sink.deliver(completion.model_copy(update={"outcome": "rejected"}))


@pytest.mark.asyncio
async def test_lost_ack_recovers_exact_closed_run_update_result(setup):
    client, journal, target, completion, _ = setup
    handle = client.get_workflow_handle.return_value
    handle.execute_update.side_effect = TimeoutError("closed after acceptance")
    await TemporalCompletionSink(client, journal, target.scope).deliver(completion)
    update_id = handle.execute_update.call_args.kwargs["id"]
    handle.get_update_handle.assert_called_once_with(
        update_id, workflow_run_id="exact-run", result_type=CommandReceipt
    )


@pytest.mark.asyncio
async def test_missing_history_remains_pending_and_reuses_update_id(setup):
    client, journal, target, completion, _ = setup
    handle = client.get_workflow_handle.return_value
    handle.execute_update.side_effect = TimeoutError()
    handle.get_update_handle.return_value.result.side_effect = LookupError("history gone")
    sink = TemporalCompletionSink(client, journal, target.scope)
    for _ in range(2):
        with pytest.raises(LookupError):
            await sink.deliver(completion)
    calls = handle.execute_update.call_args_list
    assert calls[0].kwargs["id"] == calls[1].kwargs["id"]


@pytest.mark.asyncio
async def test_unverified_receipt_never_acknowledged(setup):
    client, journal, target, completion, _ = setup
    handle = client.get_workflow_handle.return_value
    handle.execute_update.return_value = CommandReceipt("another", 1)
    sink = TemporalCompletionSink(client, journal, target.scope)
    with pytest.raises(TaskError, match="invalid completion receipt"):
        await sink.deliver(completion)
    handle.execute_update.return_value = CommandReceipt("completion", 1)
    await sink.deliver(completion)
    assert handle.execute_update.await_count == 2


def test_workflow_checks_binding_and_deduplicates_after_node_advance(monkeypatch):
    monkeypatch.setattr(
        "nexusflow.modules.durable_runtime.workflow.workflow.info",
        lambda: SimpleNamespace(workflow_id="execution", run_id="exact-run"),
    )
    instance = ProofWorkflow()
    instance.plan = ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "b" * 64, "cfg"),
        "flow",
        "sha256:" + "a" * 64,
        (Step("approve", "approval"),),
    )
    instance.node_id = "approve"
    instance.state = "waiting_approval"
    command = TaskCompletionCommand(
        "completion",
        "task",
        "acme",
        "dev",
        "execution",
        "exact-run",
        "approve",
        "sha256:" + "a" * 64,
        "release",
        "approved",
        2,
    )
    for change in (
        {"organization_id": "other"},
        {"environment_id": "prod"},
        {"workflow_id": "other"},
        {"temporal_run_id": "other"},
        {"definition_revision": "other"},
        {"definition_digest": "other"},
        {"node_invocation_id": "other"},
        {"outcome": "forged"},
        {"task_revision": True},
    ):
        with pytest.raises(ApplicationError):
            instance.validate_complete_task(replace(command, **change))
        with pytest.raises(ApplicationError):
            instance.complete_task(replace(command, **change))
        assert instance.approved is None
    instance.validate_complete_task(command)
    assert instance.approved is None
    assert instance.revision == 0
    assert instance.task_receipts == {}
    receipt = instance.complete_task(command)
    assert receipt == CommandReceipt("completion", 1)
    instance.node_id, instance.state = "end", "completed"
    instance.validate_complete_task(command)
    assert instance.complete_task(command) == receipt
    with pytest.raises(ApplicationError, match="different content"):
        instance.complete_task(replace(command, task_id="another"))
    assert instance.revision == 1
