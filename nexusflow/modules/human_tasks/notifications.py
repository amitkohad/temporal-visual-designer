"""Durable scoped reminder/escalation delivery with immutable retry payloads.

Escalation is a notification only: it never changes assignments or decisions.
Hosts call deliver_due explicitly with a bounded, durably deduplicating sink.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.notification_types import (
    DeliveryBatch,
    NotificationOccurrence,
    NotificationRecord,
    NotificationSchedule,
    TaskNotificationSink,
    utc,
)
from nexusflow.modules.human_tasks.service import TaskService, _scope
from nexusflow.modules.human_tasks.types import TaskAuthorization, TaskError, TaskView


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _stage_id(task: TaskView) -> str | None:
    if task.request.rule == "sequential":
        return task.stage_assignments[task.stage_index].stage_id
    return None


def _remaining(task: TaskView) -> tuple[str, ...]:
    stage_id = _stage_id(task)
    voted = {decision.actor_id for decision in task.decisions if decision.stage_id == stage_id}
    return tuple(actor for actor in task.eligible_approver_ids if actor not in voted)


class TaskNotificationService:
    def __init__(
        self,
        database: Path,
        authorization: TaskAuthorization,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry_seconds: int = 60,
    ) -> None:
        if type(retry_seconds) is not int or not 1 <= retry_seconds <= 86400:
            raise ValueError("Retry seconds must be an integer between 1 and 86400")
        self.clock = clock
        self.retry_seconds = retry_seconds
        self.tasks = TaskService(database, authorization)

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime):
            raise ValueError("Clock must return an aware datetime")
        return utc(value)

    def _record(self, scope: Scope, task_id: str, schedule_id: str) -> NotificationRecord | None:
        row = self.tasks._db.execute(
            "SELECT record FROM human_task_notifications WHERE org=? AND env=? AND task=? AND schedule=?",
            (*_scope(scope), task_id, schedule_id),
        ).fetchone()
        return None if row is None else NotificationRecord.model_validate_json(row[0])

    def _save(self, record: NotificationRecord) -> None:
        occurrence = record.occurrence
        self.tasks._db.execute(
            "UPDATE human_task_notifications SET record=?,status=?,next_attempt_at=? "
            "WHERE org=? AND env=? AND task=? AND schedule=?",
            (
                record.model_dump_json(),
                record.status,
                _stamp(record.next_attempt_at),
                *_scope(occurrence.scope),
                occurrence.task_id,
                record.request.schedule_id,
            ),
        )

    def schedule(
        self, scope: Scope, task_id: str, request: NotificationSchedule
    ) -> NotificationRecord:
        actor = self.tasks._actor(scope, "task.create", task_id)
        self._now()
        with self.tasks._transaction():
            task = self.tasks._get(scope, task_id)
            saved = self._record(scope, task_id, request.schedule_id)
            if saved is not None:
                if saved.request != request:
                    raise TaskError("conflict", "Schedule already has different content")
                return saved
            if task.status != "pending" or task.revision != request.expected_revision:
                raise TaskError("conflict", "Task state or revision has changed")
            count = self.tasks._db.execute(
                "SELECT COUNT(*) FROM human_task_notifications WHERE org=? AND env=? AND task=?",
                (*_scope(scope), task_id),
            ).fetchone()[0]
            if count >= 1000:
                raise TaskError("conflict", "Task notification schedule limit reached")
            recipients = _remaining(task)
            if not recipients:
                raise TaskError("conflict", "Task has no remaining approvers")
            occurrence = NotificationOccurrence(
                notification_id=str(uuid4()),
                scope=scope,
                task_id=task_id,
                run_id=task.request.run_id,
                node_invocation_id=task.request.node_invocation_id,
                definition_revision=task.request.definition_revision,
                kind=request.kind,
                due_at=request.due_at,
                recipients=recipients,
                eligible_approver_ids=task.eligible_approver_ids,
                stage_id=_stage_id(task),
            )
            record = NotificationRecord(
                request=request,
                occurrence=occurrence,
                status="pending",
                attempts=0,
                next_attempt_at=request.due_at,
            )
            self.tasks._db.execute(
                "INSERT INTO human_task_notifications(org,env,task,schedule,record,status,next_attempt_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    *_scope(scope),
                    task_id,
                    request.schedule_id,
                    record.model_dump_json(),
                    record.status,
                    _stamp(record.next_attempt_at),
                ),
            )
            updated = task.model_copy(update={"revision": task.revision + 1})
            self.tasks._db.execute(
                "UPDATE human_tasks SET view=? WHERE org=? AND env=? AND id=?",
                (updated.model_dump_json(), *_scope(scope), task_id),
            )
            self.tasks._event(
                updated,
                "notification_scheduled",
                actor,
                notification_id=occurrence.notification_id,
                stage_id=occurrence.stage_id,
            )
            return record

    def get(self, scope: Scope, task_id: str, schedule_id: str) -> NotificationRecord:
        actor = self.tasks._actor(scope, "task.read", task_id)
        with self.tasks._transaction():
            task = self.tasks._get(scope, task_id)
            self.tasks._eligible(task, actor)
            saved = self._record(scope, task_id, schedule_id)
            if saved is None:
                raise TaskError("not_found", "Notification schedule was not found")
            return saved

    def deliver_due(
        self, scope: Scope, sink: TaskNotificationSink, *, limit: int = 100
    ) -> DeliveryBatch:
        """Trusted worker boundary; callback must be bounded and durably deduplicate.

        Holding the writer transaction across delivery serializes a notification
        before concurrent completion/reassignment. Late acknowledgement or commit
        failure permits identical redelivery; no exactly-once claim is made.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limit must be an integer between 1 and 100")
        now = self._now()
        with self.tasks._lock:
            candidates = self.tasks._db.execute(
                "SELECT task,schedule FROM human_task_notifications "
                "WHERE org=? AND env=? AND status='pending' AND next_attempt_at<=? "
                "ORDER BY next_attempt_at,task,schedule LIMIT ?",
                (*_scope(scope), _stamp(now), limit),
            ).fetchall()
        delivered = failed = cancelled = 0
        for task_id, schedule_id in candidates:
            with self.tasks._transaction():
                record = self._record(scope, task_id, schedule_id)
                if record is None or record.status != "pending" or record.next_attempt_at > now:
                    continue
                task = self.tasks._get(scope, task_id)
                occurrence = record.occurrence
                if (
                    task.status != "pending"
                    or _stage_id(task) != occurrence.stage_id
                    or task.eligible_approver_ids != occurrence.eligible_approver_ids
                    or _remaining(task) != occurrence.recipients
                ):
                    self._save(record.model_copy(update={"status": "cancelled"}))
                    self.tasks._event(
                        task,
                        "notification_cancelled",
                        None,
                        notification_id=occurrence.notification_id,
                        stage_id=occurrence.stage_id,
                    )
                    cancelled += 1
                    continue
                try:
                    sink.deliver(occurrence)
                except Exception:
                    self._save(
                        record.model_copy(
                            update={
                                "attempts": record.attempts + 1,
                                "next_attempt_at": now + timedelta(seconds=self.retry_seconds),
                                "last_error": "delivery_unavailable",
                            }
                        )
                    )
                    self.tasks._event(
                        task,
                        "notification_failed",
                        None,
                        notification_id=occurrence.notification_id,
                        stage_id=occurrence.stage_id,
                    )
                    failed += 1
                else:
                    self._save(
                        record.model_copy(
                            update={
                                "status": "delivered",
                                "attempts": record.attempts + 1,
                                "last_error": None,
                            }
                        )
                    )
                    self.tasks._event(
                        task,
                        "notification_delivered",
                        None,
                        notification_id=occurrence.notification_id,
                        stage_id=occurrence.stage_id,
                    )
                    delivered += 1
        return DeliveryBatch(delivered=delivered, failed=failed, cancelled=cancelled)

    def close(self) -> None:
        self.tasks.close()
