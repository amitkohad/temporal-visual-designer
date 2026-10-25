"""Opt-in managed-task runtime composition and owned resource shutdown."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from nexusflow.apps import local_runtime as module
from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_tasks import enable_tasks
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.local import test_runtime as runtime_tests
from nexusflow.tests.local.test_runtime import description


@pytest.fixture
def boundary(monkeypatch):
    return runtime_tests.boundary.__wrapped__(monkeypatch)


@pytest.fixture
def enabled(tmp_path, boundary):
    settings = initialize(tmp_path)
    profile = enable_tasks(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        placement = identity.environments(settings.organization_id)[0]
    finally:
        identity.close()
    boundary.client.namespace = placement.temporal_namespace
    boundary.service.describe_namespace.return_value = description(placement.temporal_namespace)
    return SimpleNamespace(profile=profile, placement=placement, root=tmp_path)


@pytest.mark.asyncio
async def test_enabled_registration_and_ordered_shutdown(enabled, boundary, monkeypatch):
    owned = []
    original_tasks = module.TaskService
    original_journal = module.SQLiteCompletionJournal

    def tasks(*args):
        value = original_tasks(*args)
        owned.append(value)
        close = value.close
        value.close = lambda: (boundary.events.append("tasks-close"), close())
        return value

    def journal(*args):
        value = original_journal(*args)
        owned.append(value)
        close = value.close
        value.close = lambda: (boundary.events.append("completions-close"), close())
        return value

    @asynccontextmanager
    async def dispatch(dispatcher):
        assert dispatcher.service is owned[0]
        assert dispatcher.sink.journal is owned[1]
        boundary.events.append("dispatch-enter")
        try:
            yield dispatcher
        finally:
            boundary.events.append("dispatch-exit")

    monkeypatch.setattr(module, "TaskService", tasks)
    monkeypatch.setattr(module, "SQLiteCompletionJournal", journal)
    monkeypatch.setattr(module, "run_completion_dispatcher", dispatch)
    with pytest.raises(RuntimeError, match="caller failed"):
        async with module.local_runtime(
            enabled.root, enabled.placement, port=boundary.port, task_profile=enabled.profile
        ):
            worker = next(event[1] for event in boundary.events if isinstance(event, tuple))
            assert worker["workflows"] == [module.ProofWorkflow]
            assert worker["activities"][0].__self__.tasks is owned[0]
            raise RuntimeError("caller failed")
    assert boundary.events[-5:] == [
        "dispatch-exit",
        "worker-exit",
        "completions-close",
        "tasks-close",
        "server-exit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["profile", "placement", "tasks", "completions"])
async def test_invalid_state_fails_before_sdk_or_recreation(enabled, boundary, failure):
    profile = enabled.profile
    placement = enabled.placement
    if failure == "profile":
        profile = profile.model_copy(
            update={"installation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
        )
    elif failure == "placement":
        placement = replace(placement, task_queue="different")
    else:
        path = enabled.root / ("tasks.db" if failure == "tasks" else "task-completions.db")
        path.unlink()
    with pytest.raises(ValueError):
        async with module.local_runtime(
            enabled.root, placement, port=boundary.port, task_profile=profile
        ):
            pytest.fail("Invalid state entered")
    boundary.start.assert_not_called()
    assert not (enabled.root / "runtime-identity.json").exists()
    if failure in {"tasks", "completions"}:
        assert not path.exists()


@pytest.mark.asyncio
async def test_partial_completion_open_failure_closes_tasks(enabled, boundary, monkeypatch):
    opened = []
    original = module.TaskService

    def tasks(*args):
        value = original(*args)
        opened.append(value)
        return value

    def fail(*args):
        raise RuntimeError("completion open failed")

    monkeypatch.setattr(module, "TaskService", tasks)
    monkeypatch.setattr(module, "SQLiteCompletionJournal", fail)
    with pytest.raises(RuntimeError, match="completion open failed"):
        async with module.local_runtime(
            enabled.root, enabled.placement, port=boundary.port, task_profile=enabled.profile
        ):
            pytest.fail("Partial startup entered")
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError):
        opened[0]._db.execute("SELECT 1")
    assert boundary.events == ["server-enter", "server-exit"]


@pytest.mark.asyncio
async def test_worker_start_failure_closes_both_stores(enabled, boundary, monkeypatch):
    opened = []
    original_tasks = module.TaskService
    original_completions = module.SQLiteCompletionJournal

    def tasks(*args):
        value = original_tasks(*args)
        opened.append(value)
        return value

    def completions(*args):
        value = original_completions(*args)
        opened.append(value)
        return value

    class FailingWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("worker start failed")

        async def __aexit__(self, *args):
            pytest.fail("Unentered worker exit")

    monkeypatch.setattr(module, "TaskService", tasks)
    monkeypatch.setattr(module, "SQLiteCompletionJournal", completions)
    monkeypatch.setattr(module, "Worker", FailingWorker)
    with pytest.raises(RuntimeError, match="worker start failed"):
        async with module.local_runtime(
            enabled.root, enabled.placement, port=boundary.port, task_profile=enabled.profile
        ):
            pytest.fail("Partial worker entered")
    import sqlite3

    for value in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            value._db.execute("SELECT 1")
    assert boundary.events == ["server-enter", "server-exit"]


@pytest.mark.asyncio
async def test_cancellation_joins_dispatch_before_closing_stores(enabled, boundary, monkeypatch):
    import asyncio
    import sqlite3

    dispatcher = []
    original = module.CompletionDispatcher

    def dispatch(*args):
        value = original(*args)
        dispatcher.append(value)
        return value

    monkeypatch.setattr(module, "CompletionDispatcher", dispatch)
    with pytest.raises(asyncio.CancelledError):
        async with module.local_runtime(
            enabled.root, enabled.placement, port=boundary.port, task_profile=enabled.profile
        ):
            await asyncio.sleep(0)
            assert dispatcher[0]._running
            raise asyncio.CancelledError
    assert not dispatcher[0]._running
    assert not dispatcher[0]._context_active
    assert dispatcher[0]._context_task is None
    with pytest.raises(sqlite3.ProgrammingError):
        dispatcher[0].service._db.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        dispatcher[0].sink.journal._db.execute("SELECT 1")
    assert boundary.events[-2:] == ["worker-exit", "server-exit"]
