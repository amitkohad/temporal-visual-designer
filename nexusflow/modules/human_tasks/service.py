"""Scoped durable human decisions and at-least-once completion delivery."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.human_tasks.types import (
    AssignmentResolver,
    Completion,
    CompletionSink,
    CreateTask,
    DecisionRecord,
    DecisionRequest,
    DelegationRecord,
    DelegationRequest,
    ReassignmentRecord,
    ReassignmentRequest,
    StageAssignment,
    TaskAuthorization,
    TaskError,
    TaskEvent,
    TaskPage,
    TaskStatus,
    TaskView,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _scope(scope: Scope) -> tuple[str, str]:
    return scope.organization_id, scope.environment_id


class TaskService:
    def __init__(
        self,
        database: Path,
        authorization: TaskAuthorization,
        assignments: AssignmentResolver | None = None,
    ) -> None:
        self.authorization = authorization
        self.assignments = assignments
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), timeout=30, check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS human_tasks (
                org TEXT NOT NULL, env TEXT NOT NULL, id TEXT NOT NULL,
                run TEXT NOT NULL, invocation TEXT NOT NULL, view TEXT NOT NULL,
                PRIMARY KEY(org,env,id), UNIQUE(org,env,run,invocation));
            CREATE TABLE IF NOT EXISTS human_commands (
                org TEXT NOT NULL, env TEXT NOT NULL, task TEXT NOT NULL,
                command TEXT NOT NULL, actor TEXT NOT NULL, request TEXT NOT NULL,
                response TEXT NOT NULL, PRIMARY KEY(org,env,task,command));
            CREATE TABLE IF NOT EXISTS human_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                org TEXT NOT NULL, env TEXT NOT NULL, task TEXT NOT NULL,
                event TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS human_outbox (
                org TEXT NOT NULL, env TEXT NOT NULL, task TEXT NOT NULL,
                completion TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(org,env,task));
            CREATE TABLE IF NOT EXISTS human_task_forms (
                org TEXT NOT NULL, env TEXT NOT NULL, task TEXT NOT NULL,
                definition TEXT NOT NULL, initial_values TEXT NOT NULL,
                values_json TEXT NOT NULL, validated INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(org,env,task));
            CREATE TABLE IF NOT EXISTS human_task_notifications (
                org TEXT NOT NULL, env TEXT NOT NULL, task TEXT NOT NULL,
                schedule TEXT NOT NULL, record TEXT NOT NULL,
                status TEXT NOT NULL, next_attempt_at TEXT NOT NULL,
                PRIMARY KEY(org,env,task,schedule));
            CREATE INDEX IF NOT EXISTS human_task_notifications_due
                ON human_task_notifications(org,env,status,next_attempt_at,task,schedule);
        """)
        self._db.commit()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def _actor(self, scope: Scope, permission: str, resource: str) -> str:
        actor = self.authorization.authorize(scope, permission, resource)
        try:
            return TypeAdapter(Identifier).validate_python(actor)
        except ValidationError:
            raise TaskError("forbidden", "Authorization did not return a valid actor") from None

    def _get(self, scope: Scope, task_id: str) -> TaskView:
        row = self._db.execute(
            "SELECT view FROM human_tasks WHERE org=? AND env=? AND id=?",
            (*_scope(scope), task_id),
        ).fetchone()
        if row is None:
            raise TaskError("not_found", "Task was not found")
        return TaskView.model_validate_json(row[0])

    @staticmethod
    def _eligible(task: TaskView, actor: str) -> None:
        if actor not in task.eligible_approver_ids:
            raise TaskError("forbidden", "Actor is not an eligible approver")

    def _event(
        self,
        task: TaskView,
        kind: str,
        actor: str | None,
        assignment: ReassignmentRecord | None = None,
        notification_id: str | None = None,
        *,
        stage_id: str | None = None,
        previous_stage_id: str | None = None,
        delegation: DelegationRecord | None = None,
    ) -> None:
        event = TaskEvent.model_validate(
            {
                "event_id": str(uuid4()),
                "task_id": task.task_id,
                "scope": task.scope,
                "kind": kind,
                "actor_id": actor,
                "revision": task.revision,
                "created_at": _now(),
                "assignment": assignment,
                "notification_id": notification_id,
                "stage_id": stage_id,
                "previous_stage_id": previous_stage_id,
                "delegation": delegation,
            }
        )
        self._db.execute(
            "INSERT INTO human_events(org,env,task,event) VALUES(?,?,?,?)",
            (*_scope(task.scope), task.task_id, event.model_dump_json()),
        )

    def _resolve(
        self, scope: Scope, user_ids: tuple[str, ...], group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        try:
            if self.assignments is None:
                raise ValueError("Assignment resolver is unavailable")
            resolved = self.assignments.resolve(scope, user_ids, group_ids)
            if not isinstance(resolved, tuple) or not 1 <= len(resolved) <= 1000:
                raise ValueError("Invalid assignment size")
            return tuple(
                sorted(
                    {
                        TypeAdapter(Identifier).validate_python(value, strict=True)
                        for value in resolved
                    }
                )
            )
        except Exception:
            raise TaskError(
                "assignment_unavailable", "Eligible approvers could not be resolved"
            ) from None

    def _receipt(
        self,
        scope: Scope,
        task_id: str,
        actor: str,
        request: DecisionRequest | ReassignmentRequest | DelegationRequest,
    ) -> TaskView | None:
        row = self._db.execute(
            "SELECT actor,request,response FROM human_commands WHERE org=? AND env=? AND task=? AND command=?",
            (*_scope(scope), task_id, request.command_id),
        ).fetchone()
        if row is None:
            return None
        try:
            saved_request = type(request).model_validate_json(row[1])
        except ValidationError:
            raise TaskError("conflict", "Task command already has different content") from None
        if row[0] != actor or saved_request.model_dump_json() != request.model_dump_json():
            raise TaskError("conflict", "Task command already has different content")
        return TaskView.model_validate_json(row[2])

    def _save_command(
        self,
        task: TaskView,
        actor: str,
        request: DecisionRequest | ReassignmentRequest | DelegationRequest,
    ) -> None:
        self._db.execute(
            "UPDATE human_tasks SET view=? WHERE org=? AND env=? AND id=?",
            (task.model_dump_json(), *_scope(task.scope), task.task_id),
        )
        self._db.execute(
            "INSERT INTO human_commands VALUES(?,?,?,?,?,?,?)",
            (
                *_scope(task.scope),
                task.task_id,
                request.command_id,
                actor,
                request.model_dump_json(),
                task.model_dump_json(),
            ),
        )

    def create(self, scope: Scope, request: CreateTask) -> TaskView:
        actor = self._actor(scope, "task.create", request.run_id)
        with self._transaction():
            row = self._db.execute(
                "SELECT view FROM human_tasks WHERE org=? AND env=? AND run=? AND invocation=?",
                (*_scope(scope), request.run_id, request.node_invocation_id),
            ).fetchone()
            if row:
                task = TaskView.model_validate_json(row[0])
                if task.request != request:
                    raise TaskError("conflict", "Task invocation already has different content")
                return task
            stages = tuple(
                StageAssignment(
                    stage_id=stage.stage_id,
                    rule=stage.rule,
                    eligible_approver_ids=self._resolve(scope, stage.user_ids, stage.group_ids),
                )
                for stage in request.stages
            )
            if sum(len(stage.eligible_approver_ids) for stage in stages) > 1000:
                raise TaskError("assignment_unavailable", "Total stage assignment limit exceeded")
            ids = (
                stages[0].eligible_approver_ids
                if stages
                else self._resolve(scope, request.user_ids, request.group_ids)
            )
            task = TaskView(
                stage_assignments=stages,
                task_id=str(uuid4()),
                scope=scope,
                request=request,
                eligible_approver_ids=ids,
                status="pending",
                revision=1,
                created_at=_now(),
            )
            self._db.execute(
                "INSERT INTO human_tasks(org,env,id,run,invocation,view) VALUES(?,?,?,?,?,?)",
                (
                    *_scope(scope),
                    task.task_id,
                    request.run_id,
                    request.node_invocation_id,
                    task.model_dump_json(),
                ),
            )
            self._event(task, "created", actor)
            return task

    def get(self, scope: Scope, task_id: str) -> TaskView:
        actor = self._actor(scope, "task.read", task_id)
        with self._lock:
            task = self._get(scope, task_id)
            self._eligible(task, actor)
            return task

    def list_tasks(
        self,
        scope: Scope,
        *,
        status: TaskStatus | None = None,
        run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TaskPage:
        """List snapshot-assigned tasks using SQLite JSON1 before pagination.

        Offset traversal is bounded at 10000; no continuation beyond that bound
        is emitted. Every page performs fresh authorization.
        """
        actor = self._actor(scope, "task.read", "tasks")
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise TaskError("invalid_input", "Task page bounds are invalid")
        try:
            if run_id is not None:
                run_id = TypeAdapter(Identifier).validate_python(run_id, strict=True)
        except ValidationError:
            raise TaskError("invalid_input", "Task filters are invalid") from None
        if status is not None and (
            not isinstance(status, str) or status not in ("pending", "approved", "rejected")
        ):
            raise TaskError("invalid_input", "Task filters are invalid")
        with self._lock:
            rows = self._db.execute(
                """SELECT view FROM human_tasks
                   WHERE org=? AND env=?
                     AND EXISTS (
                         SELECT 1 FROM json_each(view, '$.eligible_approver_ids')
                         WHERE value=?
                     )
                     AND (? IS NULL OR json_extract(view, '$.status')=?)
                     AND (? IS NULL OR run=?)
                   ORDER BY json_extract(view, '$.created_at'), id
                   LIMIT ? OFFSET ?""",
                (*_scope(scope), actor, status, status, run_id, run_id, limit + 1, offset),
            ).fetchall()
            next_offset = offset + limit
            return TaskPage(
                items=tuple(TaskView.model_validate_json(row[0]) for row in rows[:limit]),
                next_offset=next_offset if len(rows) > limit and next_offset <= 10000 else None,
            )

    def decide(self, scope: Scope, task_id: str, request: DecisionRequest) -> TaskView:
        actor = self._actor(scope, "task.respond", task_id)
        with self._transaction():
            task = self._get(scope, task_id)
            if actor not in task.eligible_approver_ids:
                # A prior-stage participant may retrieve only their own accepted
                # decision receipt, never probe another participant's command.
                stored = self._db.execute(
                    "SELECT actor FROM human_commands WHERE org=? AND env=? AND task=? AND command=?",
                    (*_scope(scope), task_id, request.command_id),
                ).fetchone()
                own_decision = (
                    task.request.rule == "sequential"
                    and stored is not None
                    and stored[0] == actor
                    and any(
                        decision.actor_id == actor and decision.command_id == request.command_id
                        for decision in task.decisions
                    )
                )
                if not own_decision:
                    self._eligible(task, actor)
            receipt = self._receipt(scope, task_id, actor, request)
            if receipt is not None:
                return receipt
            self._eligible(task, actor)
            if task.revision != request.expected_revision:
                raise TaskError("conflict", "Task revision has changed")
            if task.status != "pending":
                raise TaskError("conflict", "Task is already completed")
            stage = task.stage_assignments[task.stage_index] if task.stage_assignments else None
            stage_id = stage.stage_id if stage else None
            if any(item.actor_id == actor and item.stage_id == stage_id for item in task.decisions):
                raise TaskError("conflict", "Actor has already decided this task")
            if request.decision == "approve":
                form = self._db.execute(
                    "SELECT validated FROM human_task_forms WHERE org=? AND env=? AND task=?",
                    (*_scope(scope), task_id),
                ).fetchone()
                if form is not None and form[0] != 1:
                    raise TaskError("conflict", "The task form requires a valid submission")
            record = DecisionRecord(
                stage_id=stage_id,
                command_id=request.command_id,
                actor_id=actor,
                decision=request.decision,
                revision=task.revision + 1,
                created_at=_now(),
            )
            decisions = (*task.decisions, record)
            status: TaskStatus = task.status
            next_index = task.stage_index
            ids = task.eligible_approver_ids
            stage_votes = tuple(item for item in decisions if item.stage_id == stage_id)
            effective_rule = stage.rule if stage else task.request.rule
            if request.decision == "reject":
                status = "rejected"
            elif effective_rule == "any_one" or len(stage_votes) == len(ids):
                if stage and next_index + 1 < len(task.stage_assignments):
                    next_index += 1
                    ids = task.stage_assignments[next_index].eligible_approver_ids
                else:
                    status = "approved"
            updated = task.model_copy(
                update={
                    "revision": record.revision,
                    "decisions": decisions,
                    "status": status,
                    "stage_index": next_index,
                    "eligible_approver_ids": ids,
                }
            )
            self._save_command(updated, actor, request)
            self._event(updated, "decided", actor, stage_id=stage_id)
            if next_index != task.stage_index:
                self._event(
                    updated,
                    "stage_advanced",
                    actor,
                    stage_id=updated.stage_assignments[next_index].stage_id,
                    previous_stage_id=stage_id,
                )
            if status != "pending":
                completion = Completion.model_validate(
                    {
                        "completion_id": str(uuid4()),
                        "task_id": task_id,
                        "scope": scope,
                        "run_id": task.request.run_id,
                        "node_invocation_id": task.request.node_invocation_id,
                        "definition_revision": task.request.definition_revision,
                        "outcome": status,
                        "task_revision": updated.revision,
                    }
                )
                self._db.execute(
                    "INSERT INTO human_outbox(org,env,task,completion) VALUES(?,?,?,?)",
                    (*_scope(scope), task_id, completion.model_dump_json()),
                )
            return updated

    def reassign(self, scope: Scope, task_id: str, request: ReassignmentRequest) -> TaskView:
        """Explicitly replace a pending assignment snapshot before any decision."""
        actor = self._actor(scope, "task.reassign", task_id)
        with self._transaction():
            task = self._get(scope, task_id)
            receipt = self._receipt(scope, task_id, actor, request)
            if receipt is not None:
                return receipt
            if task.revision != request.expected_revision:
                raise TaskError("conflict", "Task revision has changed")
            if task.status != "pending" or task.decisions:
                raise TaskError("conflict", "Task assignment can no longer be changed")
            if len(task.assignment_history) + len(task.delegation_history) >= 100:
                raise TaskError("conflict", "Task assignment revision limit has been reached")
            ids = self._resolve(scope, request.user_ids, request.group_ids)
            record = ReassignmentRecord(
                command_id=request.command_id,
                actor_id=actor,
                revision=task.revision + 1,
                created_at=_now(),
                user_ids=request.user_ids,
                group_ids=request.group_ids,
                previous_eligible_ids=task.eligible_approver_ids,
                eligible_approver_ids=ids,
            )
            stages = task.stage_assignments
            if stages:
                if sum(len(stage.eligible_approver_ids) for stage in stages[1:]) + len(ids) > 1000:
                    raise TaskError(
                        "assignment_unavailable", "Total stage assignment limit exceeded"
                    )
                stages = (
                    stages[0].model_copy(update={"eligible_approver_ids": ids}),
                    *stages[1:],
                )
            updated = task.model_copy(
                update={
                    "revision": record.revision,
                    "stage_assignments": stages,
                    "eligible_approver_ids": ids,
                    "assignment_history": (*task.assignment_history, record),
                }
            )
            self._save_command(updated, actor, request)
            self._event(updated, "reassigned", actor, record)
            return updated

    def delegate(self, scope: Scope, task_id: str, request: DelegationRequest) -> TaskView:
        """Replace the current actor's unvoted place with a verified directory user."""
        actor = self._actor(scope, "task.respond", task_id)
        with self._transaction():
            task = self._get(scope, task_id)
            if actor not in task.eligible_approver_ids:
                # Delegating removes the actor's current access. Only their own
                # accepted command can retrieve its immutable historical receipt.
                stored = self._db.execute(
                    "SELECT actor FROM human_commands WHERE org=? AND env=? AND task=? AND command=?",
                    (*_scope(scope), task_id, request.command_id),
                ).fetchone()
                own_delegation = (
                    stored is not None
                    and stored[0] == actor
                    and any(
                        item.actor_id == actor and item.command_id == request.command_id
                        for item in task.delegation_history
                    )
                )
                if not own_delegation:
                    self._eligible(task, actor)
            receipt = self._receipt(scope, task_id, actor, request)
            if receipt is not None:
                return receipt
            self._eligible(task, actor)
            if task.revision != request.expected_revision:
                raise TaskError("conflict", "Task revision has changed")
            if task.status != "pending":
                raise TaskError("conflict", "Task is already completed")
            stage = task.stage_assignments[task.stage_index] if task.stage_assignments else None
            stage_id = stage.stage_id if stage else None
            if any(item.actor_id == actor and item.stage_id == stage_id for item in task.decisions):
                raise TaskError("conflict", "Actor has already decided this task")
            if request.delegate_id == actor or request.delegate_id in task.eligible_approver_ids:
                raise TaskError("conflict", "Delegate must be a different unassigned user")
            if len(task.assignment_history) + len(task.delegation_history) >= 100:
                raise TaskError("conflict", "Task assignment revision limit has been reached")
            try:
                if self.assignments is None:
                    raise ValueError("Assignment resolver is unavailable")
                resolved = self.assignments.resolve(scope, (request.delegate_id,), ())
                if (
                    not isinstance(resolved, tuple)
                    or len(resolved) != 1
                    or TypeAdapter(Identifier).validate_python(resolved[0], strict=True)
                    != request.delegate_id
                ):
                    raise ValueError("Invalid delegate resolution")
            except Exception:
                raise TaskError(
                    "assignment_unavailable", "Delegate could not be resolved"
                ) from None
            ids = tuple(
                sorted(
                    request.delegate_id if value == actor else value
                    for value in task.eligible_approver_ids
                )
            )
            record = DelegationRecord(
                command_id=request.command_id,
                actor_id=actor,
                delegate_id=request.delegate_id,
                stage_id=stage_id,
                revision=task.revision + 1,
                created_at=_now(),
                previous_eligible_ids=task.eligible_approver_ids,
                eligible_approver_ids=ids,
            )
            stages = task.stage_assignments
            if stage is not None:
                stages = tuple(
                    value.model_copy(update={"eligible_approver_ids": ids})
                    if index == task.stage_index
                    else value
                    for index, value in enumerate(stages)
                )
            updated = task.model_copy(
                update={
                    "revision": record.revision,
                    "stage_assignments": stages,
                    "eligible_approver_ids": ids,
                    "delegation_history": (*task.delegation_history, record),
                }
            )
            self._save_command(updated, actor, request)
            self._event(updated, "delegated", actor, stage_id=stage_id, delegation=record)
            return updated

    def audit(self, scope: Scope, task_id: str) -> tuple[TaskEvent, ...]:
        actor = self._actor(scope, "task.read", task_id)
        with self._lock:
            self._eligible(self._get(scope, task_id), actor)
            rows = self._db.execute(
                "SELECT event FROM human_events WHERE org=? AND env=? AND task=? ORDER BY sequence",
                (*_scope(scope), task_id),
            ).fetchall()
            return tuple(TaskEvent.model_validate_json(row[0]) for row in rows)

    def pending_completions(self, scope: Scope) -> tuple[Completion, ...]:
        """Trusted worker port; must never be exposed directly to user requests."""
        with self._lock:
            rows = self._db.execute(
                "SELECT completion FROM human_outbox WHERE org=? AND env=? AND delivered=0 ORDER BY task",
                _scope(scope),
            ).fetchall()
            return tuple(Completion.model_validate_json(row[0]) for row in rows)

    def deliver_pending(self, scope: Scope, sink: CompletionSink) -> int:
        """Deliver outside transactions; uncertain acknowledgements retry the same ID."""
        delivered = 0
        for completion in self.pending_completions(scope):
            try:
                sink.deliver(completion)
            except Exception:
                raise TaskError(
                    "delivery_unavailable", "Task completion could not be delivered"
                ) from None
            with self._transaction():
                changed = self._db.execute(
                    "UPDATE human_outbox SET delivered=1 WHERE org=? AND env=? AND task=? AND delivered=0",
                    (*_scope(scope), completion.task_id),
                ).rowcount
                if changed:
                    self._event(self._get(scope, completion.task_id), "completion_delivered", None)
                    delivered += 1
        return delivered

    def close(self) -> None:
        with self._lock:
            self._db.close()
