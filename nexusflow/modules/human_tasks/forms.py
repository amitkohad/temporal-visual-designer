"""Durable task-bound forms using the shared authoritative form validator."""

import json
from pathlib import Path
from typing import Any, Literal

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_forms.form_definition import validate_form_definition
from nexusflow.modules.designer_forms.form_types import AuthorizedForm, FormError, FormView
from nexusflow.modules.designer_forms.forms import FormService
from nexusflow.modules.human_tasks.form_types import TaskFieldAccess, TaskFieldPolicy
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import TaskAuthorization, TaskError, TaskView
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import FormDefinition


def _object(payload: str, limit: int) -> dict[str, Any]:
    def constant(value: str) -> None:
        raise ValueError

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if len(payload.encode("utf-8")) > limit:
            raise ValueError
        value = json.loads(payload, parse_constant=constant, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError
        canonical(value)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 32:
                raise ValueError
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise FormError("invalid_input", "Form content must be a bounded JSON object") from None


def prepare_task_form(definition_json: str, values_json: str = "{}") -> tuple[str, str]:
    """Validate and pin bounded immutable form content without accessing storage."""
    raw = _object(definition_json, 1048576)
    try:
        definition = FormDefinition.model_validate_json(canonical(raw))
    except ValueError:
        raise FormError("invalid_definition", "Invalid form definition") from None
    issues = validate_form_definition(definition)
    if issues:
        raise FormError("invalid_definition", "Unsupported form definition", issues)
    pinned = canonical(definition.model_dump(mode="json"))
    initial = canonical(_object(values_json, 262144))
    if len(pinned.encode("utf-8")) > 1048576:
        raise FormError("invalid_definition", "Form definition exceeds the supported size")
    return pinned, initial


def bind_task_form(
    tasks: TaskService,
    scope: Scope,
    task_id: str,
    definition_json: str,
    values_json: str = "{}",
) -> int:
    """Bind using the caller's task transaction; never open or commit another writer."""
    if not tasks._db.in_transaction:
        raise RuntimeError("Task form binding requires an active task transaction")
    try:
        actor = tasks._actor(scope, "task.create", task_id)
    except (TaskError, IdentityError):
        raise
    except Exception:
        raise FormError("unavailable", "Task authorization is unavailable") from None
    task = tasks._get(scope, task_id)
    pinned, initial = prepare_task_form(definition_json, values_json)
    key = (scope.organization_id, scope.environment_id, task_id)
    row = tasks._db.execute(
        "SELECT definition,initial_values FROM human_task_forms WHERE org=? AND env=? AND task=?",
        key,
    ).fetchone()
    if row:
        if row != (pinned, initial):
            raise FormError("conflict", "Task form binding already has different content")
        return task.revision
    TaskFormService._mutable(task)
    tasks._db.execute(
        "INSERT INTO human_task_forms(org,env,task,definition,initial_values,values_json,validated) VALUES(?,?,?,?,?,?,0)",
        (*key, pinned, initial, initial),
    )
    updated = task.model_copy(update={"revision": task.revision + 1})
    tasks._db.execute(
        "UPDATE human_tasks SET view=? WHERE org=? AND env=? AND id=?",
        (updated.model_dump_json(), *key),
    )
    tasks._event(updated, "form_bound", actor)
    return updated.revision


class TaskFormService:
    def __init__(
        self,
        database: Path,
        authorization: TaskAuthorization,
        permissions: TaskFieldPolicy | None = None,
    ) -> None:
        self.tasks = TaskService(database, authorization)
        self.permissions = permissions

    def _actor(self, scope: Scope, permission: str, task_id: str) -> str:
        try:
            return self.tasks._actor(scope, permission, task_id)
        except (TaskError, IdentityError):
            raise
        except Exception:
            raise FormError("unavailable", "Task authorization is unavailable") from None

    def _row(self, scope: Scope, task_id: str) -> tuple[str, str]:
        row = self.tasks._db.execute(
            "SELECT definition,values_json FROM human_task_forms WHERE org=? AND env=? AND task=?",
            (scope.organization_id, scope.environment_id, task_id),
        ).fetchone()
        if row is None:
            raise FormError("not_found", "Task form was not found")
        return row[0], row[1]

    def _access(
        self, scope: Scope, task_id: str, actor: str, definition_json: str
    ) -> TaskFieldAccess:
        try:
            if self.permissions is None:
                raise ValueError
            access = self.permissions.access(scope, task_id, actor)
            properties = FormDefinition.model_validate_json(definition_json).data_schema[
                "properties"
            ]
            if (
                not isinstance(properties, dict)
                or not isinstance(access, TaskFieldAccess)
                or not isinstance(access.readable_fields, frozenset)
                or not isinstance(access.editable_fields, frozenset)
                or not access.editable_fields <= access.readable_fields
                or not access.readable_fields <= properties.keys()
            ):
                raise ValueError
            return access
        except Exception:
            raise FormError("unavailable", "Task field permissions are unavailable") from None

    @staticmethod
    def _mutable(task: TaskView) -> None:
        if task.status != "pending" or task.decisions:
            raise FormError("conflict", "Task form can no longer be changed")

    def _save(self, task: TaskView, actor: str, kind: str) -> int:
        updated = task.model_copy(update={"revision": task.revision + 1})
        self.tasks._db.execute(
            "UPDATE human_tasks SET view=? WHERE org=? AND env=? AND id=?",
            (
                updated.model_dump_json(),
                task.scope.organization_id,
                task.scope.environment_id,
                task.task_id,
            ),
        )
        self.tasks._event(updated, kind, actor)
        return updated.revision

    def bind(
        self, scope: Scope, task_id: str, definition_json: str, values_json: str = "{}"
    ) -> int:
        with self.tasks._transaction():
            return bind_task_form(self.tasks, scope, task_id, definition_json, values_json)

    def preview(self, scope: Scope, task_id: str) -> FormView:
        return FormService(_Gateway(self)).preview(scope, task_id)

    def submit(self, scope: Scope, task_id: str, expected_revision: int, values_json: str) -> int:
        return FormService(_Gateway(self)).submit(scope, task_id, expected_revision, values_json)

    def close(self) -> None:
        self.tasks.close()


class _Gateway:
    """Per-operation validation snapshot, never shared between requests."""

    def __init__(self, service: TaskFormService) -> None:
        self.service = service
        self.actor: str | None = None
        self.access: TaskFieldAccess | None = None

    def authorize(
        self, scope: Scope, resource_id: str, action: Literal["preview", "submit"]
    ) -> AuthorizedForm:
        service = self.service
        with service.tasks._lock:
            actor = service._actor(
                scope, "task.read" if action == "preview" else "task.respond", resource_id
            )
            task = service.tasks._get(scope, resource_id)
            service.tasks._eligible(task, actor)
            if action == "submit":
                service._mutable(task)
            definition, values = service._row(scope, resource_id)
            access = service._access(scope, resource_id, actor, definition)
            self.actor, self.access = actor, access
            editable = access.editable_fields
            if task.status != "pending" or task.decisions:
                editable = frozenset()
            return AuthorizedForm(
                definition, values, task.revision, access.readable_fields, editable
            )

    def commit(
        self, scope: Scope, resource_id: str, expected_revision: int, values_json: str
    ) -> int:
        service = self.service
        with service.tasks._transaction():
            actor = service._actor(scope, "task.respond", resource_id)
            task = service.tasks._get(scope, resource_id)
            service.tasks._eligible(task, actor)
            service._mutable(task)
            definition, _ = service._row(scope, resource_id)
            access = service._access(scope, resource_id, actor, definition)
            if actor != self.actor or access != self.access:
                raise FormError(
                    "forbidden", "Task form permissions changed; reload before submitting"
                )
            if task.revision != expected_revision:
                raise FormError("conflict", "Task revision has changed")
            # Partial patches can each fit the limit while their merged value
            # does not. Never persist values that a subsequent preview cannot load.
            _object(values_json, 262144)
            service.tasks._db.execute(
                "UPDATE human_task_forms SET values_json=?,validated=1 WHERE org=? AND env=? AND task=?",
                (values_json, scope.organization_id, scope.environment_id, resource_id),
            )
            return service._save(task, actor, "form_updated")
