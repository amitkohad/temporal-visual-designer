"""Rerun identity, immutable provenance and local crash recovery acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.service import DebugSessionService
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.tests.debugger.test_sessions import setup  # noqa: F401


@pytest.fixture
def reruns(setup):  # noqa: F811
    setup[3].maximum = 10
    return setup


@pytest.mark.asyncio
async def test_rerun_selects_explicit_revision_and_inputs_without_changing_parent(reruns):
    scope, service, request, repository, _, runtime, readiness = reruns
    parent = await service.start(scope, "purchase", request)
    original = repository.get(scope, "purchase", parent.session_id)
    content = json.loads(service.projects.revision(scope, "purchase", 1).content_json)
    content["workflows"][0]["input_schema"]["description"] = "Edited workflow inputs"
    service.projects.save(scope, "purchase", 1, json.dumps(content), "Edit", "alice")
    rerun = request.model_copy(update={"operation_id": "rerun", "source_revision": 2, "input": {}})
    child = await service.rerun(scope, "purchase", parent.session_id, rerun)
    assert child.session_id != parent.session_id
    assert child.execution_id != parent.execution_id
    assert child.source_revision == 2
    assert child.definition_digest != parent.definition_digest
    assert repository.get(scope, "purchase", parent.session_id) == original
    assert json.loads(runtime.starts[-1][2].input_json) == {}
    assert runtime.controls == []
    assert readiness.calls == 2
    assert await service.rerun(scope, "purchase", parent.session_id, rerun) == child
    assert len(runtime.starts) == 2


@pytest.mark.asyncio
async def test_rerun_lost_ack_reopens_same_intent_without_live_source_or_readiness(
    reruns, tmp_path
):
    scope, service, request, repository, auth, runtime, readiness = reruns
    parent = await service.start(scope, "purchase", request)
    request = request.model_copy(update={"operation_id": "rerun"})
    runtime.fail = True
    with pytest.raises(DebugError) as error:
        await service.rerun(scope, "purchase", parent.session_id, request)
    assert error.value.code == "unavailable"
    pending = repository.operation(scope, "purchase", "rerun")
    assert pending.session.state == "dispatch_pending"
    with SQLiteDebugSessionRepositoryContext(tmp_path / "debug.db") as reopened:
        resumed = DebugSessionService(reopened, None, auth, runtime, service.placement, None)
        runtime.fail = False
        child = await resumed.rerun(scope, "purchase", parent.session_id, request)
        assert child.execution_id == pending.session.execution_id
        assert runtime.starts[-1] == runtime.starts[-2]
        assert readiness.calls == 2
        runtime.fail = True
        assert await resumed.rerun(scope, "purchase", parent.session_id, request) == child


class SQLiteDebugSessionRepositoryContext(SQLiteDebugSessionRepository):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@pytest.mark.asyncio
async def test_operation_cannot_be_adopted_between_normal_start_or_different_parents(reruns):
    scope, service, request, repository, _, runtime, _ = reruns
    parent = await service.start(scope, "purchase", request)
    other = await service.start(
        scope, "purchase", request.model_copy(update={"operation_id": "other-parent"})
    )
    child_request = request.model_copy(update={"operation_id": "child"})
    child = await service.rerun(scope, "purchase", parent.session_id, child_request)
    for action in (
        service.start(scope, "purchase", child_request),
        service.rerun(scope, "purchase", other.session_id, child_request),
        service.rerun(scope, "purchase", other.session_id, request),
        service.rerun(scope, "purchase", parent.session_id, request),
        service.rerun(
            scope, "purchase", parent.session_id, child_request.model_copy(update={"input": {}})
        ),
    ):
        with pytest.raises(DebugError) as error:
            await action
        assert error.value.code == "conflict"
    assert len(runtime.starts) == 3
    assert repository.get(scope, "purchase", child.session_id).session == child


@pytest.mark.asyncio
async def test_parent_owner_fresh_permissions_scope_and_workflow_checked(reruns):
    scope, service, request, _, auth, runtime, _ = reruns
    parent = await service.start(scope, "purchase", request)
    request = request.model_copy(update={"operation_id": "rerun"})
    auth.actor = "bob"
    with pytest.raises(DebugError) as error:
        await service.rerun(scope, "purchase", parent.session_id, request)
    assert error.value.code == "forbidden"
    auth.actor, auth.denied = "alice", True
    with pytest.raises(PermissionError):
        await service.rerun(scope, "purchase", parent.session_id, request)
    auth.denied = False
    for environment in ("uat", "prod"):
        with pytest.raises(DebugError) as error:
            await service.rerun(
                scope.model_copy(update={"environment_id": environment}),
                "purchase",
                parent.session_id,
                request,
            )
        assert error.value.code == "forbidden"
    with pytest.raises(DebugError) as error:
        await service.rerun(
            scope,
            "purchase",
            parent.session_id,
            request.model_copy(update={"workflow_id": "other"}),
        )
    assert error.value.code == "conflict"
    service.placement = replace(service.placement, task_queue="moved")
    with pytest.raises(DebugError, match="placement"):
        await service.rerun(scope, "purchase", parent.session_id, request)
    assert len(runtime.starts) == 1


@pytest.mark.asyncio
async def test_pending_parent_must_be_acknowledged_before_rerun(reruns):
    scope, service, request, repository, _, runtime, _ = reruns
    runtime.fail = True
    with pytest.raises(DebugError):
        await service.start(scope, "purchase", request)
    parent = repository.operation(scope, "purchase", request.operation_id)
    with pytest.raises(DebugError, match="acknowledged"):
        await service.rerun(
            scope,
            "purchase",
            parent.session.session_id,
            request.model_copy(update={"operation_id": "rerun"}),
        )
    assert len(runtime.starts) == 1


@pytest.mark.asyncio
async def test_lineage_insert_failure_rolls_back_child_intent_and_retry_succeeds(reruns):
    scope, service, request, repository, _, runtime, _ = reruns
    parent = await service.start(scope, "purchase", request)
    repository.connection.execute(
        "CREATE TRIGGER fail_lineage BEFORE INSERT ON debug_reruns "
        "BEGIN SELECT RAISE(ABORT,'private storage failure'); END"
    )
    repository.connection.commit()
    request = request.model_copy(update={"operation_id": "rerun"})
    with pytest.raises(DebugError) as error:
        await service.rerun(scope, "purchase", parent.session_id, request)
    assert error.value.code == "unavailable" and "private" not in str(error.value)
    assert repository.operation(scope, "purchase", "rerun") is None
    assert repository.connection.execute("SELECT count(*) FROM debug_reruns").fetchone()[0] == 0
    assert len(runtime.starts) == 1
    repository.connection.execute("DROP TRIGGER fail_lineage")
    repository.connection.commit()
    assert (await service.rerun(scope, "purchase", parent.session_id, request)).state == "started"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["checksum", "parent", "foreign-parent", "missing-table"])
async def test_corrupt_lineage_rejects_retry_before_dispatch(reruns, mutation):
    scope, service, request, repository, _, runtime, _ = reruns
    parent = await service.start(scope, "purchase", request)
    child_request = request.model_copy(update={"operation_id": "rerun"})
    await service.rerun(scope, "purchase", parent.session_id, child_request)
    if mutation == "checksum":
        repository.connection.execute("UPDATE debug_reruns SET checksum='bad'")
    elif mutation == "parent":
        repository.connection.execute("UPDATE debug_reruns SET parent_session_id='missing'")
    elif mutation == "foreign-parent":
        repository.connection.execute("DELETE FROM debug_sessions WHERE operation='operation'")
    else:
        repository.connection.execute("DROP TABLE debug_reruns")
    repository.connection.commit()
    with pytest.raises(DebugError):
        await service.start(scope, "purchase", child_request)
    assert len(runtime.starts) == 2


@pytest.mark.asyncio
async def test_legacy_database_reopens_additively_with_unchanged_checksum(reruns, tmp_path):
    scope, service, request, repository, auth, runtime, readiness = reruns
    parent = await service.start(scope, "purchase", request)
    original = repository.get(scope, "purchase", parent.session_id)
    checksum = repository.connection.execute("SELECT checksum FROM debug_sessions").fetchone()[0]
    repository.connection.execute("DROP TABLE debug_reruns")
    repository.connection.commit()
    with SQLiteDebugSessionRepositoryContext(tmp_path / "debug.db") as reopened:
        assert reopened.get(scope, "purchase", parent.session_id) == original
        assert (
            reopened.connection.execute("SELECT checksum FROM debug_sessions").fetchone()[0]
            == checksum
        )
        upgraded = DebugSessionService(
            reopened, service.projects, auth, runtime, service.placement, readiness
        )
        assert await upgraded.start(scope, "purchase", request) == parent
        child = await upgraded.rerun(
            scope,
            "purchase",
            parent.session_id,
            request.model_copy(update={"operation_id": "rerun"}),
        )
        assert child.execution_id != parent.execution_id


@pytest.mark.asyncio
async def test_two_parent_claims_race_atomically_at_project_global_operation(reruns, tmp_path):
    scope, service, request, repository, _, _, _ = reruns
    first = await service.start(scope, "purchase", request)
    second = await service.start(
        scope, "purchase", request.model_copy(update={"operation_id": "other-parent"})
    )
    request = request.model_copy(update={"operation_id": "shared-rerun"})
    candidate = service._prepare(scope, "purchase", request, "alice")
    barrier = Barrier(2)

    def claim(parent):
        with SQLiteDebugSessionRepositoryContext(tmp_path / "debug.db") as connection:
            barrier.wait(timeout=10)
            try:
                return connection.prepare(candidate, parent.session_id)
            except DebugError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (first, second)))
    assert sum(result == "conflict" for result in results) == 1
    assert repository.connection.execute("SELECT count(*) FROM debug_reruns").fetchone()[0] == 1
    assert repository.connection.execute("SELECT count(*) FROM debug_sessions").fetchone()[0] == 3
