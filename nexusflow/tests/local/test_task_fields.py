"""Synthetic field policy cannot create state or grant cross-scope task access."""

import json
import sqlite3

import pytest

from nexusflow.apps.local_bootstrap import USER_ID, initialize, open_backend
from nexusflow.apps.local_tasks import LocalTaskFields, enable_tasks
from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask
from nexusflow.tests.local.test_bootstrap import Runtime

SCOPE = Scope(organization_id="local-demo", environment_id="dev")


class Worker:
    def authorize(self, scope, permission, resource_id):
        return "local-task-worker"


class Directory:
    def resolve(self, scope, user_ids, group_ids):
        return (USER_ID,)


def test_field_policy_reads_pinned_fields_with_strict_local_identity(tmp_path):
    initialize(tmp_path)
    enable_tasks(tmp_path)
    core = TaskService(tmp_path / "tasks.db", Worker(), Directory())
    forms = TaskFormService(tmp_path / "tasks.db", Worker())
    try:
        task = core.create(
            SCOPE,
            CreateTask(
                run_id="run",
                node_invocation_id="approve",
                definition_revision="v1",
                rule="any_one",
                user_ids=(USER_ID,),
            ),
        )
        forms.bind(
            SCOPE,
            task.task_id,
            json.dumps(
                {
                    "schema_version": "0.1",
                    "form_id": "test",
                    "data_schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            ),
        )
        with open_backend(tmp_path, Runtime()) as backend:
            policy = backend.task_fields
            assert policy is not None
            access = policy.access(SCOPE, task.task_id, USER_ID)
            assert access.readable_fields == access.editable_fields == frozenset({"name"})
            for scope, actor in (
                (SCOPE, "foreign"),
                (Scope(organization_id="other", environment_id="dev"), USER_ID),
            ):
                with pytest.raises(PermissionError):
                    policy.access(scope, task.task_id, actor)
    finally:
        forms.close()
        core.close()


def test_field_policy_missing_database_never_creates_file(tmp_path):
    policy = LocalTaskFields(tmp_path, SCOPE)
    with pytest.raises(sqlite3.OperationalError):
        policy.access(SCOPE, "task", USER_ID)
    assert not (tmp_path / "tasks.db").exists()


def test_disabled_profile_does_not_install_field_policy(tmp_path):
    initialize(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend:
        assert backend.task_fields is None
