"""Explicit local task opt-in preserves installation and fresh authorization."""

import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.apps.local_bootstrap import USER_ID, initialize, issue_token, open_backend
from nexusflow.apps.local_tasks import (
    LocalTaskAssignments,
    LocalTaskProfile,
    LocalTaskWorkerAuthorization,
    enable_tasks,
    load_task_profile,
)
from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.tests.local.test_bootstrap import Runtime

SCOPE = Scope(organization_id="local-demo", environment_id="dev")
BASE = "/api/v1/organizations/local-demo/environments/dev/tasks"


def test_legacy_and_enabled_backend(tmp_path):
    settings = initialize(tmp_path)
    assert load_task_profile(tmp_path, settings) is None
    with open_backend(tmp_path, Runtime()) as backend:
        assert backend.tasks_database is None
    profile = enable_tasks(tmp_path)
    assert profile == load_task_profile(tmp_path, settings)
    with open_backend(tmp_path, Runtime()) as backend:
        assert backend.tasks_database == tmp_path / "tasks.db"
        assert backend.task_assignments.resolve(SCOPE, (USER_ID,), ()) == (USER_ID,)
        assert backend.projects.revision(SCOPE, "welcome").number == 1
    assert initialize(tmp_path) == settings


def test_idempotent_enable_preserves_revocation_and_data(tmp_path):
    initialize(tmp_path)
    profile = enable_tasks(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.revoke_grant(SCOPE, "local-task-participant", expected_revision=1)
        identity.revoke_grant(SCOPE, "local-author", expected_revision=1)
    finally:
        identity.close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert enable_tasks(tmp_path) == profile
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_missing_marker_does_not_restore_tombstoned_grant(tmp_path):
    initialize(tmp_path)
    enable_tasks(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.revoke_grant(SCOPE, "local-task-participant", expected_revision=1)
    finally:
        identity.close()
    for name in ("human-tasks.json", "tasks.db", "task-completions.db"):
        (tmp_path / name).unlink()
    with pytest.raises(IdentityError):
        enable_tasks(tmp_path)
    assert not (tmp_path / "tasks.db").exists()
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        assert not any(g.role == "task_participant" for g in identity.grants(SCOPE))
    finally:
        identity.close()


@pytest.mark.parametrize("filename", ["tasks.db", "task-completions.db"])
def test_unmarked_partial_state_fails_closed(tmp_path, filename):
    settings = initialize(tmp_path)
    (tmp_path / filename).write_bytes(b"preserve")
    with pytest.raises(ValueError):
        enable_tasks(tmp_path)
    with pytest.raises(ValueError):
        load_task_profile(tmp_path, settings)
    assert (tmp_path / filename).read_bytes() == b"preserve"


@pytest.mark.parametrize("filename", ["tasks.db", "task-completions.db"])
def test_enabled_file_loss_fails_without_recreating(tmp_path, filename):
    settings = initialize(tmp_path)
    enable_tasks(tmp_path)
    (tmp_path / filename).unlink()
    with pytest.raises(ValueError):
        load_task_profile(tmp_path, settings)
    with pytest.raises(ValueError):
        with open_backend(tmp_path, Runtime()):
            pass
    assert not (tmp_path / filename).exists()


@pytest.mark.parametrize(
    "mutation", ["installation_id", "organization_id", "environment_id", "extra", "path"]
)
def test_marker_cannot_repoint_installation_or_scope(tmp_path, mutation):
    settings = initialize(tmp_path)
    enable_tasks(tmp_path)
    path = tmp_path / "human-tasks.json"
    marker = json.loads(path.read_text())
    if mutation == "installation_id":
        marker[mutation] = str(uuid4())
    elif mutation in ("organization_id", "environment_id"):
        marker["scope"][mutation] = "other"
    elif mutation == "path":
        marker["tasks_database"] = "../tasks.db"
    else:
        marker["unexpected"] = True
    path.write_text(json.dumps(marker))
    with pytest.raises(ValueError):
        load_task_profile(tmp_path, settings)


@pytest.mark.parametrize(
    "table",
    [
        "human_outbox",
        "human_commands",
        "human_events",
        "human_task_forms",
        "human_task_notifications",
    ],
)
def test_corrupt_or_incomplete_database_rejected(tmp_path, table):
    settings = initialize(tmp_path)
    enable_tasks(tmp_path)
    with sqlite3.connect(tmp_path / "tasks.db") as db:
        db.execute("DROP TABLE " + table)
    before = (tmp_path / "tasks.db").read_bytes()
    with pytest.raises(ValueError):
        load_task_profile(tmp_path, settings)
    with pytest.raises(ValueError):
        with open_backend(tmp_path, Runtime()):
            pass
    assert (tmp_path / "tasks.db").read_bytes() == before


def test_assignment_and_worker_scope_are_restricted():
    resolver = LocalTaskAssignments(SCOPE)
    worker = LocalTaskWorkerAuthorization(SCOPE)
    assert resolver.resolve(SCOPE, (USER_ID, USER_ID), ()) == (USER_ID,)
    assert worker.authorize(SCOPE, "task.create", "invocation") == "local-task-worker"
    other = Scope(organization_id="other", environment_id="dev")
    for scope, users, groups in (
        (other, (USER_ID,), ()),
        (SCOPE, (), ()),
        (SCOPE, ("unknown",), ()),
        (SCOPE, (USER_ID,), ("group",)),
    ):
        with pytest.raises(PermissionError):
            resolver.resolve(scope, users, groups)
    for scope, permission, resource in (
        (other, "task.create", "id"),
        (SCOPE, "task.read", "id"),
        (SCOPE, "task.respond", "id"),
        (SCOPE, "task.create", ""),
    ):
        with pytest.raises((PermissionError, ValueError)):
            worker.authorize(scope, permission, resource)
    for cls in (LocalTaskAssignments, LocalTaskWorkerAuthorization):
        with pytest.raises(ValueError):
            cls(Scope(organization_id="local-demo", environment_id="prod"))
    with pytest.raises(ValueError):
        LocalTaskProfile(installation_id=str(uuid4()).upper(), scope=SCOPE)


def test_signed_api_participation_and_fresh_revocation(tmp_path):
    initialize(tmp_path)
    enable_tasks(tmp_path)
    tasks = TaskService(
        tmp_path / "tasks.db", LocalTaskWorkerAuthorization(SCOPE), LocalTaskAssignments(SCOPE)
    )
    try:
        task = tasks.create(
            SCOPE,
            CreateTask(
                run_id="run",
                node_invocation_id="approval",
                definition_revision="release",
                rule="any_one",
                user_ids=(USER_ID,),
            ),
        )
    finally:
        tasks.close()
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(BASE + "/" + task.task_id).status_code == 200
        result = client.post(
            BASE + "/" + task.task_id + "/decisions",
            json={"command_id": "approve", "expected_revision": 1, "decision": "approve"},
        )
        assert result.status_code == 200, result.text
        backend.identity.repository.revoke_grant(
            SCOPE, "local-task-participant", expected_revision=1
        )
        assert client.get(BASE + "/" + task.task_id).status_code == 403
        assert (
            client.post(
                BASE + "/" + task.task_id + "/decisions",
                json={"command_id": "approve", "expected_revision": 1, "decision": "approve"},
            ).status_code
            == 403
        )


def test_enable_does_not_recreate_removed_membership_or_author_grant(tmp_path):
    initialize(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.revoke_grant(SCOPE, "local-author", expected_revision=1)
        identity.remove_membership(
            "local-demo", "11111111-1111-4111-8111-111111111111", "user", USER_ID
        )
    finally:
        identity.close()
    with pytest.raises(IdentityError):
        enable_tasks(tmp_path)
    assert not (tmp_path / "human-tasks.json").exists()
    assert not (tmp_path / "tasks.db").exists()
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert (
            client.get("/api/v1/organizations/local-demo/environments/dev/projects").status_code
            == 403
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["human_commands", "human_events"])
async def test_runtime_rejects_missing_receipts_or_audit_before_start(tmp_path, monkeypatch, table):
    from nexusflow.apps import local_runtime as runtime_module

    initialize(tmp_path)
    profile = enable_tasks(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        placement = next(
            item for item in identity.environments("local-demo") if item.scope == SCOPE
        )
    finally:
        identity.close()
    with sqlite3.connect(tmp_path / "tasks.db") as db:
        db.execute("DROP TABLE " + table)
    before = (tmp_path / "tasks.db").read_bytes()
    monkeypatch.setattr(runtime_module, "_executable", lambda configured: "unused")

    def unexpected(*args, **kwargs):
        raise AssertionError("Runtime must not reserve or start after task profile corruption")

    monkeypatch.setattr(runtime_module, "_reserve_identity", unexpected)
    with pytest.raises(ValueError):
        async with runtime_module.local_runtime(tmp_path, placement, task_profile=profile):
            pytest.fail("Corrupt task state must not become runnable")
    assert (tmp_path / "tasks.db").read_bytes() == before
    assert not (tmp_path / "dispatch.db").exists()
