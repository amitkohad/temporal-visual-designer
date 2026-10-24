"""Worker metadata, atomic visibility and creation retry acceptance."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.durable_runtime.human_completions import SQLiteCompletionJournal
from nexusflow.modules.durable_runtime.task_creation import TaskCreationActivities
from nexusflow.modules.durable_runtime.types import ReleaseBinding, TaskCreationCall
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import TaskError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    scope = Scope(organization_id="acme", environment_id="dev")
    auth = Mock()
    auth.authorize.return_value = "worker"
    directory = Mock()
    directory.resolve.return_value = ("alice",)
    tasks = TaskService(tmp_path / "tasks.db", auth, directory)
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    client = SimpleNamespace(
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="localhost:7233")),
        namespace="acme-dev",
    )
    info = SimpleNamespace(
        workflow_id="execution", workflow_run_id="exact-run", workflow_namespace="acme-dev"
    )
    monkeypatch.setattr(
        "nexusflow.modules.durable_runtime.task_creation.activity.info", lambda: info
    )
    adapter = TaskCreationActivities(tasks, journal, client, scope)
    call = TaskCreationCall(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config"),
        "execution",
        "exact-run",
        "approval",
        "sha256:" + "b" * 64,
        '{"rule":"any_one","user_ids":["alice"]}',
    )
    yield adapter, call, info, auth
    journal.close()
    tasks._db.close()


@pytest.mark.asyncio
async def test_retry_after_lost_result_pins_target(setup):
    adapter, call, _, auth = setup
    task_id = await adapter.create(call)
    assert await adapter.create(call) == task_id
    target = adapter.journal.target(adapter.scope, task_id)
    assert target.temporal_run_id == call.temporal_run_id
    assert target.definition_revision == call.binding.release_id
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 1
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 1
    assert auth.authorize.call_count == 2
    with pytest.raises(TaskError):
        adapter.journal.claim(target, "wrong-route", "command")
    assert adapter.journal.claim(target, adapter.route, "command") is None
    with pytest.raises(TaskError):
        adapter.journal.claim(target, adapter.route, "changed-command")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workflow_id", "workflow_run_id", "workflow_namespace"])
async def test_spoofed_metadata_cannot_create(setup, field):
    adapter, call, info, _ = setup
    setattr(info, field, "wrong")
    with pytest.raises(PermissionError):
        await adapter.create(call)
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_changed_scope_payload_and_revoked_permission(setup):
    adapter, call, _, auth = setup
    await adapter.create(call)
    with pytest.raises(PermissionError):
        await adapter.create(replace(call, binding=replace(call.binding, environment_id="prod")))
    with pytest.raises(TaskError, match="different content"):
        await adapter.create(
            replace(call, approval_json='{"rule":"all_approvers","user_ids":["alice"]}')
        )
    with pytest.raises(TaskError, match="another completion destination"):
        await adapter.create(replace(call, definition_digest="sha256:" + "c" * 64))
    auth.authorize.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await adapter.create(call)


@pytest.mark.asyncio
async def test_registration_failure_rolls_back_and_retry_reuses_orphan(setup, monkeypatch):
    adapter, call, _, _ = setup
    register = adapter.journal.register
    ids = []

    def fail(target, *, route=None):
        ids.append(target.task_id)
        register(target, route=route)
        raise RuntimeError("lost registration result")

    monkeypatch.setattr(adapter.journal, "register", fail)
    with pytest.raises(RuntimeError):
        await adapter.create(call)
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 0
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 0
    monkeypatch.setattr(adapter.journal, "register", register)
    assert await adapter.create(call) == ids[0]


@pytest.mark.asyncio
async def test_audit_failure_does_not_publish_task(setup):
    adapter, call, _, _ = setup
    adapter.tasks._db.execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON human_events BEGIN SELECT RAISE(ABORT,'audit'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await adapter.create(call)
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 0
    assert (
        adapter.journal._db.execute("SELECT COUNT(*) FROM temporal_task_completions").fetchone()[0]
        == 0
    )


def test_shared_database_rejected(setup):
    adapter, _, _, _ = setup
    journal = SQLiteCompletionJournal(adapter.tasks.database)
    try:
        with pytest.raises(ValueError, match="separate"):
            TaskCreationActivities(adapter.tasks, journal, adapter.client, adapter.scope)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_concurrent_bound_creation_one_task_and_event(setup):
    adapter, call, _, _ = setup
    await adapter.create(call)
    task = adapter.tasks._db.execute("SELECT view FROM human_tasks").fetchone()[0]
    from nexusflow.modules.human_tasks.types import TaskView

    request = TaskView.model_validate_json(task).request
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: adapter.tasks.create_bound(adapter.scope, request, lambda _: None),
                range(8),
            )
        )
    assert len({task.task_id for task in results}) == 1
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        {"runtime_version": "unknown"},
        {"release_digest": "bad"},
        {"configuration_revision": ""},
        {"release_id": ""},
    ],
)
async def test_invalid_binding_rejected_before_visibility(setup, update):
    adapter, call, _, _ = setup
    with pytest.raises(ValueError):
        await adapter.create(replace(call, binding=replace(call.binding, **update)))
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_journal_cannot_repin_existing_destination(setup):
    adapter, call, _, _ = setup
    task_id = await adapter.create(call)
    target = adapter.journal.target(adapter.scope, task_id)
    with pytest.raises(TaskError, match="destination changed"):
        adapter.journal.register(target, route="other")
    assert adapter.journal.claim(target, adapter.route, "command") is None
