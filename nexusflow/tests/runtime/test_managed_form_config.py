"""Pinned managed-form configuration and atomic worker binding acceptance."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.durable_runtime.human_completions import SQLiteCompletionJournal
from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan
from nexusflow.modules.durable_runtime.task_creation import TaskCreationActivities
from nexusflow.modules.durable_runtime.types import ReleaseBinding, TaskCreationCall
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import TaskView

FORM = {
    "schema_version": "0.1",
    "form_id": "approval-form",
    "data_schema": {
        "type": "object",
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
        "additionalProperties": False,
    },
}


def configuration(form=None):
    value = {"rule": "any_one", "user_ids": ["alice"]}
    if form is not None:
        value["form"] = form
    return value


def compile_configuration(config):
    definition = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "purchasing.workflow.json").read_text()
    )
    definition["nodes"][1]["input_mapping"] = {"human_task": config}
    return compile_plan(
        json.dumps(definition),
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config"),
        Scope(organization_id="acme", environment_id="dev"),
    )


def test_no_form_canonical_bytes_remain_unchanged():
    plan = compile_configuration(configuration())
    assert (
        plan.steps[1].task_approval_json
        == '{"group_ids":[],"rule":"any_one","stages":[],"user_ids":["alice"]}'
    )
    assert (
        compile_configuration({**configuration(), "form": None}).steps[1].task_approval_json
        == plan.steps[1].task_approval_json
    )


def test_form_is_pinned_canonically_and_changes_definition_digest():
    plain = compile_configuration(configuration())
    form = {"definition": FORM, "initial_values": {"amount": 42}}
    plan = compile_configuration(configuration(form))
    assert plan.definition_digest != plain.definition_digest
    assert json.loads(plan.steps[1].task_approval_json)["form"]["initial_values"] == {"amount": 42}
    assert (
        plan.steps[1].task_approval_json
        == compile_configuration(configuration(dict(reversed(list(form.items())))))
        .steps[1]
        .task_approval_json
    )


@pytest.mark.parametrize(
    "form",
    [
        [],
        {},
        {"definition": FORM, "initial_values": []},
        {"definition": FORM, "initial_values": "forged"},
        {"definition": FORM, "initial_values": {"amount": float("nan")}},
        {"definition": {**FORM, "attachment_policy": {"enabled": True}}},
        {"definition": FORM, "extra": True},
        {"definition": {**FORM, "layout": {"extra": 1}}},
    ],
)
def test_invalid_inline_form_rejected_by_preflight(form):
    with pytest.raises(PlanError):
        compile_configuration(configuration(form))


@pytest.fixture
def host(tmp_path, monkeypatch):
    scope = Scope(organization_id="acme", environment_id="dev")
    auth, directory = Mock(), Mock()
    auth.authorize.return_value = "worker"
    directory.resolve.return_value = ("alice",)
    tasks = TaskService(tmp_path / "tasks.db", auth, directory)
    journal = SQLiteCompletionJournal(tmp_path / "journal.db")
    client = SimpleNamespace(
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="localhost:7233")),
        namespace="acme-dev",
    )
    monkeypatch.setattr(
        "nexusflow.modules.durable_runtime.task_creation.activity.info",
        lambda: SimpleNamespace(
            workflow_id="execution", workflow_run_id="exact-run", workflow_namespace="acme-dev"
        ),
    )
    adapter = TaskCreationActivities(tasks, journal, client, scope)
    call = TaskCreationCall(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config"),
        "execution",
        "exact-run",
        "approval",
        "sha256:" + "b" * 64,
        json.dumps(configuration({"definition": FORM, "initial_values": {"amount": 42}})),
    )
    yield adapter, call
    journal.close()
    tasks._db.close()


@pytest.mark.asyncio
async def test_worker_atomically_binds_form_and_retries_identically(host):
    adapter, call = host
    task_id = await adapter.create(call)
    assert await adapter.create(call) == task_id
    row = adapter.tasks._db.execute("SELECT view FROM human_tasks").fetchone()
    assert TaskView.model_validate_json(row[0]).revision == 2
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 2
    values = adapter.tasks._db.execute("SELECT initial_values FROM human_task_forms").fetchone()[0]
    assert json.loads(values) == {"amount": 42}
    assert adapter.journal.target(adapter.scope, task_id).temporal_run_id == "exact-run"
    changed = json.loads(call.approval_json)
    changed["form"]["initial_values"]["amount"] = 43
    with pytest.raises(FormError, match="different content"):
        await adapter.create(replace(call, approval_json=json.dumps(changed)))
    assert (
        adapter.tasks._db.execute("SELECT initial_values FROM human_task_forms").fetchone()[0]
        == values
    )


@pytest.mark.asyncio
async def test_form_audit_failure_rolls_back_task_and_form(host):
    adapter, call = host
    adapter.tasks._db.execute(
        "CREATE TRIGGER fail_form BEFORE INSERT ON human_task_forms BEGIN SELECT RAISE(ABORT,'form'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await adapter.create(call)
    for table in ("human_tasks", "human_task_forms", "human_events"):
        assert adapter.tasks._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    adapter.tasks._db.execute("DROP TRIGGER fail_form")
    assert await adapter.create(call)


@pytest.mark.asyncio
async def test_invalid_form_host_never_registers_destination(host):
    adapter, call = host
    malformed = json.loads(call.approval_json)
    malformed["form"]["initial_values"] = []
    with pytest.raises(ValueError):
        await adapter.create(replace(call, approval_json=json.dumps(malformed)))
    assert (
        adapter.journal._db.execute("SELECT COUNT(*) FROM temporal_task_completions").fetchone()[0]
        == 0
    )


def test_form_depth_and_global_definition_size_bounds():
    nested = {}
    for _ in range(34):
        nested = {"child": nested}
    with pytest.raises(PlanError):
        compile_configuration(configuration({"definition": FORM, "initial_values": nested}))
    with pytest.raises(PlanError, match="payload limit"):
        compile_configuration(
            configuration({"definition": FORM, "initial_values": {"amount": "x" * 131072}})
        )
