"""Session intent, owner boundaries and restart acceptance with trusted fake runtime ports."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope, WorkflowDefinition
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.service import DebugSessionService
from nexusflow.modules.designer_debugger.types import DebugControl, DebugError, DebugStart
from nexusflow.modules.durable_runtime.types import CommandReceipt, Snapshot
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.types import ProjectContent


class Authorization:
    actor = "alice"
    denied = False

    def authorize(self, scope, permission, project):
        assert permission == "debug.execute"
        if self.denied:
            raise PermissionError("Denied")
        return self.actor


class Readiness:
    calls = 0
    issues = ()

    def validate(self, scope, release, request):
        self.calls += 1
        return self.issues

    def retain(self, scope, release, usage):
        pass


class Runtime:
    fail = False
    wrong_binding = False

    def __init__(self):
        self.starts = []
        self.controls = []

    async def start(self, placement, execution_id, plan):
        self.starts.append((placement, execution_id, plan))
        if self.fail:
            raise OSError("Lost acknowledgement")
        return "run-1"

    async def snapshot(self, placement, execution_id, run_id):
        assert run_id == "run-1"
        plan = self.starts[-1][2]
        return Snapshot(
            "wrong" if self.wrong_binding else plan.binding.release_id,
            plan.definition_digest,
            "proof.v1",
            "start",
            "paused",
            0,
            (),
        )

    async def control(self, placement, execution_id, run_id, command):
        self.controls.append((placement, execution_id, run_id, command))
        return CommandReceipt(command.command_id, 1)


@pytest.fixture
def setup(tmp_path):
    scope = Scope(organization_id="acme", environment_id="dev")
    placement = Environment(scope, "acme-dev", "debug-worker")
    project = SQLiteProjectRepository(tmp_path / "projects.db")
    definition = WorkflowDefinition.model_validate_json(
        (Path(__file__).parents[1] / "fixtures" / "purchasing.workflow.json").read_text()
    )
    value = definition.model_dump(mode="json")
    value["nodes"] = [value["nodes"][0], value["nodes"][-1]]
    value["edges"] = [
        {
            "source_node_id": "start",
            "source_port": "out",
            "target_node_id": "finish",
            "target_port": "in",
        }
    ]
    definition = WorkflowDefinition.model_validate_json(json.dumps(value))
    content = ProjectContent(schema_version="0.1", workflows=(definition,))
    project.create(scope, "purchase", "Purchase", content.model_dump_json(), "Initial", "alice")
    repository = SQLiteDebugSessionRepository(tmp_path / "debug.db", maximum_sessions_per_project=2)
    auth, runtime, readiness = Authorization(), Runtime(), Readiness()
    service = DebugSessionService(repository, project, auth, runtime, placement, readiness)
    request = DebugStart(
        operation_id="operation",
        source_revision=1,
        workflow_id=definition.workflow_id,
        configuration_revision="config-1",
        input={"private": "input"},
        mode="real",
    )
    yield scope, service, request, repository, auth, runtime, readiness
    repository.close()
    project.close()


@pytest.mark.asyncio
async def test_start_retry_status_and_control_are_bound_and_redacted(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    assert session.state == "started"
    assert session.actor_id == "alice"
    assert "private" not in session.model_dump_json()
    assert await service.start(scope, "purchase", request) == session
    assert len(runtime.starts) == readiness.calls == 1
    assert runtime.starts[0][2].debug is True
    assert json.loads(runtime.starts[0][2].input_json) == request.input
    status = await service.status(scope, "purchase", session.session_id)
    assert status.snapshot.state == "paused"
    receipt = await service.control(
        scope,
        "purchase",
        session.session_id,
        DebugControl(command_id="step", node_id="start", action="step_over", expected_revision=0),
    )
    assert receipt.revision == 1
    assert runtime.controls[0][2] == "run-1"
    auth.actor = "bob"
    for operation in (
        service.start(scope, "purchase", request),
        service.status(scope, "purchase", session.session_id),
    ):
        with pytest.raises(DebugError, match="another actor"):
            await operation


@pytest.mark.asyncio
async def test_lost_ack_freezes_plan_and_retries_same_execution_after_restart(setup, tmp_path):
    scope, service, request, repository, auth, runtime, readiness = setup
    runtime.fail = True
    with pytest.raises(DebugError, match="unavailable"):
        await service.start(scope, "purchase", request)
    frozen = repository.operation(scope, "purchase", request.operation_id)
    assert frozen.session.state == "dispatch_pending"
    pending = frozen.session.session_id
    with pytest.raises(DebugError, match="acknowledged"):
        await service.status(scope, "purchase", pending)
    restarted = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        new_service = DebugSessionService(restarted, None, auth, runtime, service.placement, None)
        runtime.fail = False
        session = await new_service.start(scope, "purchase", request)
        assert session.temporal_run_id == "run-1"
        assert runtime.starts[0] == runtime.starts[1]
        assert readiness.calls == 1
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_conflicts_route_changes_scope_and_runtime_binding(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    with pytest.raises(DebugError, match="another request"):
        await service.start(scope, "purchase", request.model_copy(update={"input": {}}))
    service.placement = replace(service.placement, task_queue="moved")
    with pytest.raises(DebugError, match="placement"):
        await service.status(scope, "purchase", session.session_id)
    service.placement = replace(service.placement, task_queue="debug-worker")
    runtime.wrong_binding = True
    with pytest.raises(DebugError, match="binding"):
        await service.status(scope, "purchase", session.session_id)
    for environment in ("uat", "prod"):
        with pytest.raises(DebugError, match="Dev"):
            await service.start(
                scope.model_copy(update={"environment_id": environment}), "purchase", request
            )
    with pytest.raises(DebugError, match="not found"):
        await service.status(
            scope.model_copy(update={"organization_id": "other"}), "purchase", session.session_id
        )
    auth.denied = True
    with pytest.raises(PermissionError):
        await service.start(scope, "purchase", request)


@pytest.mark.asyncio
async def test_quota_and_preflight_fail_without_dispatch(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    with pytest.raises(DebugError, match="not found"):
        await service.start(
            scope, "purchase", request.model_copy(update={"workflow_id": "missing"})
        )
    assert not runtime.starts
    await service.start(scope, "purchase", request)
    await service.start(scope, "purchase", request.model_copy(update={"operation_id": "second"}))
    with pytest.raises(DebugError, match="capacity"):
        await service.start(scope, "purchase", request.model_copy(update={"operation_id": "third"}))
    assert len(runtime.starts) == 2


@pytest.mark.asyncio
async def test_corrupt_frozen_plan_is_rejected_before_redispatch(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    runtime.fail = True
    with pytest.raises(DebugError):
        await service.start(scope, "purchase", request)
    repository.connection.execute(
        "UPDATE debug_sessions SET plan=replace(plan, 'private', 'changed')"
    )
    repository.connection.commit()
    runtime.fail = False
    with pytest.raises(DebugError, match="integrity"):
        await service.start(scope, "purchase", request)
    assert len(runtime.starts) == 1


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "2"])
def test_invalid_quota_rejected(tmp_path, capacity):
    with pytest.raises(ValueError):
        SQLiteDebugSessionRepository(tmp_path / "debug.db", capacity)


@pytest.mark.asyncio
async def test_original_run_ack_cannot_be_replaced(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    with pytest.raises(DebugError, match="another run"):
        repository.acknowledge(scope, "purchase", session.session_id, "other-run")
    assert repository.get(scope, "purchase", session.session_id).session == session


@pytest.mark.asyncio
async def test_control_identity_reserved_and_completed_receipt_survives_runtime_failure(
    setup, monkeypatch, tmp_path
):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    command = DebugControl(
        command_id="step", node_id="start", action="step_over", expected_revision=0
    )
    assert repository.claim_command(scope, "purchase", session.session_id, command) is None
    with pytest.raises(DebugError, match="another request"):
        await service.control(
            scope, "purchase", session.session_id, command.model_copy(update={"action": "continue"})
        )
    receipt = await service.control(scope, "purchase", session.session_id, command)
    runtime.wrong_binding = True
    restarted = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        service.repository = restarted
        assert await service.control(scope, "purchase", session.session_id, command) == receipt
        assert len(runtime.controls) == 1
    finally:
        restarted.close()
        service.repository = repository


@pytest.mark.asyncio
async def test_run_identifier_corruption_fails_integrity(setup):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    repository.connection.execute(
        "UPDATE debug_sessions SET session=replace(session, 'run-1', 'run-2')"
    )
    repository.connection.commit()
    with pytest.raises(DebugError, match="integrity"):
        await service.status(scope, "purchase", session.session_id)


@pytest.mark.asyncio
async def test_command_lost_local_ack_uses_same_reserved_request(setup, monkeypatch):
    scope, service, request, repository, auth, runtime, readiness = setup
    session = await service.start(scope, "purchase", request)
    command = DebugControl(
        command_id="step", node_id="start", action="step_over", expected_revision=0
    )
    acknowledge = repository.acknowledge_command

    def lost(*args):
        raise OSError("Lost local acknowledgement")

    monkeypatch.setattr(repository, "acknowledge_command", lost)
    with pytest.raises(DebugError, match="unavailable"):
        await service.control(scope, "purchase", session.session_id, command)
    monkeypatch.setattr(repository, "acknowledge_command", acknowledge)
    receipt = await service.control(scope, "purchase", session.session_id, command)
    assert receipt.command_id == "step"
    assert runtime.controls[0] == runtime.controls[1]
