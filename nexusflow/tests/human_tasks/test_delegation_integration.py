"""Delegation preserves voted form data and invalidates obsolete recipients."""

import json
from datetime import timedelta

import pytest

from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.human_tasks.form_types import TaskFieldAccess
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.human_tasks.notification_types import NotificationSchedule
from nexusflow.modules.human_tasks.notifications import TaskNotificationService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask, DelegationRequest, TaskError
from nexusflow.tests.human_tasks.test_acceptance import SCOPE, Authorization, decision
from nexusflow.tests.human_tasks.test_notifications import Clock, Sink


class Directory:
    def resolve(self, scope, user_ids, group_ids):
        assert scope == SCOPE and not group_ids
        return user_ids


class Fields:
    def access(self, scope, task_id, actor_id):
        return TaskFieldAccess(frozenset({"reason"}), frozenset({"reason"}))


def test_partial_vote_delegation_preserves_form_and_targets_only_uncast_votes(tmp_path):
    path = tmp_path / "tasks.db"
    auth, clock = Authorization(), Clock()
    core = TaskService(path, auth, Directory())
    forms = TaskFormService(path, auth, Fields())
    notifications = TaskNotificationService(path, auth, clock=clock)
    try:
        task = core.create(
            SCOPE,
            CreateTask(
                run_id="run",
                node_invocation_id="approval",
                definition_revision="r1",
                rule="all_approvers",
                user_ids=("alice", "bob"),
            ),
        )
        bound = forms.bind(
            SCOPE,
            task.task_id,
            json.dumps(
                {
                    "form_id": "request",
                    "schema_version": "0.1",
                    "data_schema": {
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                        "additionalProperties": False,
                    },
                }
            ),
        )
        submitted = forms.submit(SCOPE, task.task_id, bound, '{"reason":"purchase"}')
        auth.actor = "bob"
        voted = core.decide(SCOPE, task.task_id, decision("bob-vote", submitted))
        auth.actor = "alice"
        old = notifications.schedule(
            SCOPE,
            task.task_id,
            NotificationSchedule(
                schedule_id="old",
                expected_revision=voted.revision,
                due_at=clock.now + timedelta(seconds=10),
                kind="reminder",
            ),
        )
        assert old.occurrence.recipients == ("alice",)
        delegated = core.delegate(
            SCOPE,
            task.task_id,
            DelegationRequest(
                command_id="delegate",
                expected_revision=voted.revision + 1,
                delegate_id="carol",
            ),
        )
        assert delegated.decisions == voted.decisions
        with pytest.raises(TaskError) as removed:
            forms.preview(SCOPE, task.task_id)
        assert removed.value.code == "forbidden"
        current = notifications.schedule(
            SCOPE,
            task.task_id,
            NotificationSchedule(
                schedule_id="current",
                expected_revision=delegated.revision,
                due_at=clock.now + timedelta(seconds=10),
                kind="reminder",
            ),
        )
        assert current.occurrence.recipients == ("carol",)
        auth.actor = "carol"
        view = forms.preview(SCOPE, task.task_id)
        assert json.loads(view.values_json) == {"reason": "purchase"}
        assert all(not field.editable for field in view.fields)
        with pytest.raises(FormError) as frozen:
            forms.submit(SCOPE, task.task_id, view.revision, '{"reason":"changed"}')
        assert frozen.value.code == "conflict"
        clock.now += timedelta(seconds=10)
        sink = Sink()
        batch = notifications.deliver_due(SCOPE, sink)
        assert (batch.cancelled, batch.delivered) == (1, 1)
        assert sink.calls == [current.occurrence]
        final = core.decide(SCOPE, task.task_id, decision("carol-vote", view.revision))
        assert final.status == "approved"
        assert {vote.actor_id for vote in final.decisions} == {"bob", "carol"}
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        notifications.close()
        forms.close()
        core.close()
