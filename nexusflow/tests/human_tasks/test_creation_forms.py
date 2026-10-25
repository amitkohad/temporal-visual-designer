"""Atomic provisioning of task forms before managed approval visibility."""

import json
import sqlite3

import pytest

from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.human_tasks.forms import bind_task_form, prepare_task_form
from nexusflow.modules.human_tasks.types import TaskError
from nexusflow.tests.human_tasks.test_task_forms import SCOPE, definition, services, vote


def test_helper_requires_transaction(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, *_):
        with pytest.raises(RuntimeError, match="active task transaction"):
            bind_task_form(core, SCOPE, task.task_id, definition())
        assert core.get(SCOPE, task.task_id).revision == 1


def test_bound_task_and_form_are_invisible_until_atomic_commit(tmp_path):
    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, original, *_):
        request = original.request.model_copy(update={"node_invocation_id": "managed"})
        with sqlite3.connect(path) as observer:

            def provision(task):
                assert (
                    observer.execute(
                        "SELECT COUNT(*) FROM human_tasks WHERE id=?", (task.task_id,)
                    ).fetchone()[0]
                    == 0
                )
                assert bind_task_form(core, SCOPE, task.task_id, definition()) == 2
                assert observer.execute("SELECT COUNT(*) FROM human_task_forms").fetchone()[0] == 0

            created = core.create_bound(SCOPE, request, provision)
            assert created.revision == 2
            assert observer.execute("SELECT COUNT(*) FROM human_task_forms").fetchone()[0] == 1
            assert (
                core.create_bound(
                    SCOPE,
                    request,
                    lambda task: bind_task_form(core, SCOPE, task.task_id, definition()),
                ).revision
                == 2
            )


def test_binding_and_late_callback_failure_roll_back_everything(tmp_path):
    path = tmp_path / "tasks.db"
    attempted = []
    with services(path) as (core, forms, original, *_):
        request = original.request.model_copy(update={"node_invocation_id": "managed"})

        def provision(task):
            attempted.append(task.task_id)
            bind_task_form(core, SCOPE, task.task_id, definition())
            raise RuntimeError("lost provisioning")

        with pytest.raises(RuntimeError, match="lost provisioning"):
            core.create_bound(SCOPE, request, provision)
        assert core._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 1
        assert core._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 1
        assert core._db.execute("SELECT COUNT(*) FROM human_task_forms").fetchone()[0] == 0
        created = core.create_bound(
            SCOPE, request, lambda task: bind_task_form(core, SCOPE, task.task_id, definition())
        )
        assert created.task_id == attempted[0]
        assert created.revision == 2


def test_retry_preserves_submitted_and_terminal_form_and_rejects_conflicting_snapshot(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, original, *_):
        request = original.request.model_copy(update={"node_invocation_id": "managed"})
        initial = '{"name":"Ada","amount":10}'

        def binder(task):
            return bind_task_form(core, SCOPE, task.task_id, definition(), initial)

        created = core.create_bound(SCOPE, request, binder)
        assert forms.submit(SCOPE, created.task_id, 2, '{"amount":20}') == 3
        assert core.create_bound(SCOPE, request, binder).revision == 3
        terminal = core.decide(SCOPE, created.task_id, vote(3))
        assert terminal.status == "approved"
        assert core.create_bound(SCOPE, request, binder) == terminal
        row = core._db.execute(
            "SELECT values_json,validated FROM human_task_forms WHERE task=?", (created.task_id,)
        ).fetchone()
        assert json.loads(row[0])["amount"] == 20
        assert row[1] == 1
        with pytest.raises(FormError, match="different content"):
            core.create_bound(
                SCOPE,
                request,
                lambda task: bind_task_form(
                    core, SCOPE, task.task_id, definition(), '{"name":"Changed"}'
                ),
            )
        changed = json.loads(definition())
        changed["form_id"] = "another"
        with pytest.raises(FormError, match="different content"):
            core.create_bound(
                SCOPE,
                request,
                lambda task: bind_task_form(
                    core, SCOPE, task.task_id, json.dumps(changed), initial
                ),
            )
        assert core.get(SCOPE, created.task_id) == terminal


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', "[]", '{"x":NaN}'])
def test_prepare_rejects_invalid_initial_json(payload):
    with pytest.raises(FormError):
        prepare_task_form(definition(), payload)


def test_prepare_is_canonical_and_storage_free():
    pinned, initial = prepare_task_form(definition(), '{"amount":10,"name":"Ada"}')
    assert prepare_task_form(pinned, '{"name":"Ada","amount":10}') == (pinned, initial)


def test_invalid_form_rolls_back_task_before_visibility(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, original, *_):
        request = original.request.model_copy(update={"node_invocation_id": "invalid"})
        with pytest.raises(FormError):
            core.create_bound(
                SCOPE,
                request,
                lambda task: bind_task_form(core, SCOPE, task.task_id, '{"schema_version":"0.1"}'),
            )
        assert core._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 1
        assert core._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 1


def test_binding_checks_fresh_authorization_even_for_exact_retry(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, original, auth, *_):
        with core._transaction():
            assert bind_task_form(core, SCOPE, original.task_id, definition()) == 2
        auth.allowed = False
        with core._transaction(), pytest.raises(TaskError, match="Revoked"):
            bind_task_form(core, SCOPE, original.task_id, definition())


@pytest.mark.parametrize(
    "initial", ["x" * 262145, json.dumps({"nested": "x" * 262144})], ids=["raw", "json"]
)
def test_prepare_bounds_initial_content(initial):
    with pytest.raises(FormError):
        prepare_task_form(definition(), initial)


def test_legacy_bind_masks_provider_failure_and_rolls_back(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, auth, *_):

        def unavailable(scope, permission, resource_id):
            raise RuntimeError("private provider credential")

        auth.authorize = unavailable
        with pytest.raises(FormError, match="Task authorization is unavailable") as error:
            forms.bind(SCOPE, task.task_id, definition())
        assert error.value.code == "unavailable"
        assert "private" not in str(error.value)
        assert error.value.__suppress_context__
        assert not forms.tasks._db.in_transaction
        assert core._db.execute("SELECT COUNT(*) FROM human_task_forms").fetchone()[0] == 0
        assert core._db.execute("SELECT COUNT(*) FROM human_events").fetchone()[0] == 1
