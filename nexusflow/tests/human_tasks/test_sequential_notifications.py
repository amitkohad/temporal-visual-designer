"""Stage boundaries invalidate reminders even when approver IDs are unchanged."""

import json
from datetime import timedelta

from nexusflow.modules.human_tasks.notification_types import (
    NotificationOccurrence,
    NotificationSchedule,
)
from nexusflow.modules.human_tasks.notifications import TaskNotificationService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import ApprovalStage, CreateTask
from nexusflow.tests.human_tasks.test_acceptance import SCOPE, Authorization, Directory, decision
from nexusflow.tests.human_tasks.test_notifications import Clock, Sink


def test_existing_nonsequential_notification_keeps_its_serialized_retry_payload():
    legacy = {
        "notification_id": "existing",
        "scope": SCOPE.model_dump(),
        "task_id": "task",
        "run_id": "run",
        "node_invocation_id": "approval",
        "definition_revision": "r1",
        "kind": "reminder",
        "due_at": "2026-10-24T00:00:00Z",
        "recipients": ["alice"],
        "eligible_approver_ids": ["alice"],
    }
    occurrence = NotificationOccurrence.model_validate_json(json.dumps(legacy))
    assert occurrence.stage_id is None
    assert occurrence.model_dump(mode="json") == legacy


def test_same_approver_next_stage_cancels_old_reminder_and_can_receive_new_one(tmp_path):
    path = tmp_path / "tasks.db"
    auth, clock = Authorization(), Clock()
    core = TaskService(path, auth, Directory(("alice",)))
    reminders = TaskNotificationService(path, auth, clock=clock)
    try:
        task = core.create(
            SCOPE,
            CreateTask(
                run_id="run",
                node_invocation_id="approve",
                definition_revision="r1",
                rule="sequential",
                stages=(
                    ApprovalStage(stage_id="manager", rule="any_one", user_ids=("alice",)),
                    ApprovalStage(stage_id="finance", rule="all_approvers", user_ids=("alice",)),
                ),
            ),
        )
        old = reminders.schedule(
            SCOPE,
            task.task_id,
            NotificationSchedule(
                schedule_id="manager-reminder",
                expected_revision=1,
                due_at=clock.now + timedelta(seconds=10),
                kind="reminder",
            ),
        )
        advanced = core.decide(SCOPE, task.task_id, decision("manager", 2))
        assert advanced.stage_index == 1 and advanced.status == "pending"
        current = reminders.schedule(
            SCOPE,
            task.task_id,
            NotificationSchedule(
                schedule_id="finance-reminder",
                expected_revision=advanced.revision,
                due_at=clock.now + timedelta(seconds=10),
                kind="reminder",
            ),
        )
        assert old.occurrence.recipients == current.occurrence.recipients == ("alice",)
        assert (old.occurrence.stage_id, current.occurrence.stage_id) == ("manager", "finance")
        clock.now += timedelta(seconds=10)
        sink = Sink()
        result = reminders.deliver_due(SCOPE, sink)
        assert (result.cancelled, result.delivered, result.failed) == (1, 1, 0)
        assert sink.calls == [current.occurrence]
        assert reminders.get(SCOPE, task.task_id, "manager-reminder").status == "cancelled"
        assert core.pending_completions(SCOPE) == ()
        final = core.decide(SCOPE, task.task_id, decision("finance", 4))
        assert final.status == "approved"
        assert len(core.pending_completions(SCOPE)) == 1
        events = core.audit(SCOPE, task.task_id)
        cancelled = next(event for event in events if event.kind == "notification_cancelled")
        assert cancelled.stage_id == "manager"
        delivered = next(event for event in events if event.kind == "notification_delivered")
        assert delivered.stage_id == "finance"
    finally:
        reminders.close()
        core.close()
