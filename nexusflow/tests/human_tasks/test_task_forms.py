"""Independent real-SQLite acceptance for task-bound configured forms."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.human_tasks.form_types import TaskFieldAccess
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import (
    CreateTask,
    DecisionRequest,
    ReassignmentRequest,
    TaskError,
)

SCOPE = Scope(organization_id="org", environment_id="dev")
OTHER = Scope(organization_id="foreign", environment_id="dev")
ERRORS = (TaskError, FormError)
FIELDS = frozenset({"name", "amount", "token"})


def definition():
    return json.dumps(
        {
            "schema_version": "0.1",
            "form_id": "purchase",
            "data_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "amount": {"type": "number", "minimum": 1},
                    "token": {"type": "string", "writeOnly": True},
                    "private": {"type": "string"},
                },
                "required": ["name", "amount"],
                "additionalProperties": False,
            },
            "layout": {"field_order": ["name", "amount", "token", "private"]},
        }
    )


class Authorization:
    actor = "alice"
    allowed = True

    def authorize(self, scope, permission, resource_id):
        if not self.allowed:
            raise TaskError("forbidden", "Revoked")
        return self.actor


class Directory:
    members = ("alice", "bob")

    def resolve(self, scope, user_ids, group_ids):
        return self.members


class Policy:
    allowed = True
    calls = 0
    revoke_after = None

    def access(self, scope, task_id, actor):
        self.calls += 1
        if not self.allowed or (self.revoke_after and self.calls > self.revoke_after):
            raise TaskError("forbidden", "Field policy revoked")
        return TaskFieldAccess(readable_fields=FIELDS, editable_fields=FIELDS)


@contextmanager
def services(path, rule="any_one"):
    auth, directory, policy = Authorization(), Directory(), Policy()
    core = TaskService(path, auth, directory)
    forms = TaskFormService(path, auth, policy)
    task = core.create(
        SCOPE,
        CreateTask(
            run_id="run",
            node_invocation_id="node",
            definition_revision="release-1",
            rule=rule,
            group_ids=("purchasing",),
        ),
    )
    try:
        yield core, forms, task, auth, directory, policy
    finally:
        forms.close()
        core.close()


def vote(revision, outcome="approve"):
    return DecisionRequest(
        command_id=f"vote-{revision}-{outcome}", expected_revision=revision, decision=outcome
    )


def test_bind_is_immutable_idempotent_and_reopen_pins_initial_snapshot(tmp_path):
    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, task, auth, directory, policy):
        revision = forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        assert revision == 2
        assert core.get(SCOPE, task.task_id).request == task.request
        assert forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}') == 2
        with pytest.raises(ERRORS):
            forms.bind(SCOPE, task.task_id, definition(), '{"name":"Other","amount":10}')
        assert forms.submit(SCOPE, task.task_id, 2, '{"amount":20}') == 3
        assert forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}') == 3
        reopened = TaskFormService(path, auth, policy)
        try:
            assert json.loads(reopened.preview(SCOPE, task.task_id).values_json)["amount"] == 20
        finally:
            reopened.close()


def test_redaction_validation_and_approval_share_one_revision(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, auth, directory, policy):
        forms.bind(
            SCOPE, task.task_id, definition(), '{"token":"WRITE-SECRET","private":"HIDDEN-SECRET"}'
        )
        preview = forms.preview(SCOPE, task.task_id)
        assert [field.field_id for field in preview.fields] == ["name", "amount", "token"]
        assert "SECRET" not in repr(preview)
        with pytest.raises(ERRORS):
            core.decide(SCOPE, task.task_id, vote(2))
        with pytest.raises(FormError) as caught:
            forms.submit(SCOPE, task.task_id, 2, '{"name":"Ada"}')
        assert caught.value.code == "validation_failed"
        assert core.get(SCOPE, task.task_id).revision == 2
        assert forms.submit(SCOPE, task.task_id, 2, '{"name":"Ada","amount":5}') == 3
        assert core.decide(SCOPE, task.task_id, vote(3)).status == "approved"
        assert "SECRET" not in repr(core.get(SCOPE, task.task_id))
        assert "SECRET" not in repr(core.audit(SCOPE, task.task_id))


@pytest.mark.parametrize(
    "patch", ['{"amount":0}', '{"private":"guess"}', '{"unknown":1}', '{"amount":NaN}']
)
def test_invalid_and_unauthorized_patches_do_not_mutate(tmp_path, patch):
    with services(tmp_path / "tasks.db") as (core, forms, task, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        before = forms.preview(SCOPE, task.task_id)
        events = core.audit(SCOPE, task.task_id)
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 2, patch)
        assert forms.preview(SCOPE, task.task_id) == before
        assert core.audit(SCOPE, task.task_id) == events


def test_stale_foreign_and_revoked_actor_cannot_change_forms(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, auth, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 1, '{"amount":20}')
        with pytest.raises(ERRORS):
            forms.submit(OTHER, task.task_id, 2, '{"amount":20}')
        auth.actor = "outsider"
        with pytest.raises(ERRORS):
            forms.preview(SCOPE, task.task_id)
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 2, '{"amount":20}')
        auth.actor, auth.allowed = "alice", False
        with pytest.raises(ERRORS):
            forms.preview(SCOPE, task.task_id)
        auth.allowed = True
        assert core.get(SCOPE, task.task_id).revision == 2


def test_group_refresh_removes_old_actor_form_access(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, auth, directory, _):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        directory.members = ("charlie",)
        core.reassign(
            SCOPE,
            task.task_id,
            ReassignmentRequest(
                command_id="refresh",
                expected_revision=2,
                group_ids=("purchasing",),
            ),
        )
        with pytest.raises(ERRORS):
            forms.preview(SCOPE, task.task_id)
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 3, '{"amount":20}')
        auth.actor = "charlie"
        assert forms.preview(SCOPE, task.task_id).revision == 3


def test_first_vote_freezes_form_even_while_all_approvers_task_is_pending(tmp_path):
    with services(tmp_path / "tasks.db", "all_approvers") as (core, forms, task, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        forms.submit(SCOPE, task.task_id, 2, "{}")
        assert core.decide(SCOPE, task.task_id, vote(3)).status == "pending"
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 4, '{"amount":20}')
        assert core.get(SCOPE, task.task_id).revision == 4


def test_reject_does_not_require_valid_form_submission(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, *_):
        forms.bind(SCOPE, task.task_id, definition())
        assert core.decide(SCOPE, task.task_id, vote(2, "reject")).status == "rejected"


def test_fresh_field_policy_rechecked_before_commit(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, auth, directory, policy):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        policy.calls = 0
        policy.revoke_after = 1
        with pytest.raises(ERRORS):
            forms.submit(SCOPE, task.task_id, 2, '{"amount":20}')
        assert policy.calls >= 2
        assert core.get(SCOPE, task.task_id).revision == 2
        policy.revoke_after = None
        assert json.loads(forms.preview(SCOPE, task.task_id).values_json)["amount"] == 10


def test_real_audit_insert_failure_rolls_back_form_and_revision(tmp_path):
    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, task, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        before = forms.preview(SCOPE, task.task_id)
        events = core.audit(SCOPE, task.task_id)
        with sqlite3.connect(path) as fault:
            fault.execute(
                "CREATE TRIGGER fail_form_event BEFORE INSERT ON human_events "
                "WHEN json_extract(NEW.event, '$.kind') = 'form_updated' "
                "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
            )
        with pytest.raises(sqlite3.DatabaseError):
            forms.submit(SCOPE, task.task_id, 2, '{"amount":20}')
        assert forms.preview(SCOPE, task.task_id) == before
        assert core.get(SCOPE, task.task_id).revision == 2
        assert core.audit(SCOPE, task.task_id) == events


def test_decision_and_submission_race_has_single_winner(tmp_path):
    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, task, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        forms.submit(SCOPE, task.task_id, 2, "{}")
        barrier = Barrier(2)

        def compete(submit):
            auth = Authorization()
            other_core = TaskService(path, auth, Directory())
            other_forms = TaskFormService(path, auth, Policy())
            try:
                barrier.wait(timeout=10)
                try:
                    if submit:
                        other_forms.submit(SCOPE, task.task_id, 3, '{"amount":20}')
                    else:
                        other_core.decide(SCOPE, task.task_id, vote(3))
                    return True
                except ERRORS:
                    return False
            finally:
                other_forms.close()
                other_core.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(compete, (True, False))) == 1
        final = core.get(SCOPE, task.task_id)
        assert final.revision == 4
        assert len(core.pending_completions(SCOPE)) == (1 if final.status == "approved" else 0)


def test_missing_policy_denies_preview_and_submission_without_mutation(tmp_path):
    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, task, auth, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        denied = TaskFormService(path, auth)
        try:
            with pytest.raises(ERRORS):
                denied.preview(SCOPE, task.task_id)
            with pytest.raises(ERRORS):
                denied.submit(SCOPE, task.task_id, 2, '{"amount":20}')
        finally:
            denied.close()
        assert core.get(SCOPE, task.task_id).revision == 2


def test_reduced_field_permissions_between_validation_and_commit_deny_write(tmp_path):
    class ReducingPolicy(Policy):
        def access(self, scope, task_id, actor):
            self.calls += 1
            return TaskFieldAccess(
                readable_fields=FIELDS,
                editable_fields=FIELDS if self.calls == 1 else frozenset({"name"}),
            )

    path = tmp_path / "tasks.db"
    with services(path) as (core, forms, task, auth, *_):
        forms.bind(SCOPE, task.task_id, definition(), '{"name":"Ada","amount":10}')
        guarded = TaskFormService(path, auth, ReducingPolicy())
        try:
            with pytest.raises(ERRORS):
                guarded.submit(SCOPE, task.task_id, 2, '{"amount":20}')
        finally:
            guarded.close()
        assert core.get(SCOPE, task.task_id).revision == 2
        assert json.loads(forms.preview(SCOPE, task.task_id).values_json)["amount"] == 10


def test_bound_json_rule_arrays_drive_visibility_and_conditional_validation(tmp_path):
    configured = json.loads(definition())
    configured["visibility_rules"] = [
        {
            "schema_version": "0.1",
            "field_id": "amount",
            "when": {"properties": {"name": {"const": "Manager"}}, "required": ["name"]},
        }
    ]
    configured["validation_rules"] = [
        {
            "schema_version": "0.1",
            "data_schema": {
                "if": {"properties": {"name": {"const": "Manager"}}, "required": ["name"]},
                "then": {"properties": {"amount": {"minimum": 100}}},
            },
        }
    ]
    with services(tmp_path / "tasks.db") as (core, forms, task, *_):
        assert (
            forms.bind(SCOPE, task.task_id, json.dumps(configured), '{"name":"Ada","amount":10}')
            == 2
        )
        assert "amount" not in [
            field.field_id for field in forms.preview(SCOPE, task.task_id).fields
        ]
        with pytest.raises(FormError) as caught:
            forms.submit(SCOPE, task.task_id, 2, '{"name":"Manager","amount":50}')
        assert caught.value.code == "validation_failed"
        assert core.get(SCOPE, task.task_id).revision == 2
        assert forms.submit(SCOPE, task.task_id, 2, '{"name":"Manager","amount":100}') == 3
        shown = forms.preview(SCOPE, task.task_id)
        assert "amount" in [field.field_id for field in shown.fields]
        assert json.loads(shown.values_json)["amount"] == 100


def test_individually_bounded_patch_cannot_overflow_merged_stored_values(tmp_path):
    with services(tmp_path / "tasks.db") as (core, forms, task, *_):
        initial = json.dumps({"name": "n" * 140000, "amount": 10})
        patch = json.dumps({"token": "t" * 140000})
        assert len(initial.encode()) < 262144
        assert len(patch.encode()) < 262144
        forms.bind(SCOPE, task.task_id, definition(), initial)
        before = forms.preview(SCOPE, task.task_id)
        events = core.audit(SCOPE, task.task_id)
        with pytest.raises(FormError) as caught:
            forms.submit(SCOPE, task.task_id, 2, patch)
        assert caught.value.code == "invalid_input"
        assert forms.preview(SCOPE, task.task_id) == before
        assert core.get(SCOPE, task.task_id).revision == 2
        assert core.audit(SCOPE, task.task_id) == events
        with pytest.raises(ERRORS):
            core.decide(SCOPE, task.task_id, vote(2))
