"""Explicit synthetic local human-task profile; never a production directory."""

import sqlite3
from contextlib import ExitStack
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, field_validator

from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID, LocalSettings, load_settings
from nexusflow.contracts.models import Contract, Identifier, Scope
from nexusflow.modules.durable_runtime.human_completions import SQLiteCompletionJournal
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant


class LocalTaskProfile(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo-human-tasks"] = "local-demo-human-tasks"
    installation_id: str
    scope: Scope
    tasks_database: Literal["tasks.db"] = "tasks.db"
    completion_database: Literal["task-completions.db"] = "task-completions.db"

    @field_validator("installation_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Installation ID must be a canonical UUID")
        return value

    @field_validator("scope")
    @classmethod
    def dev_only(cls, value: Scope) -> Scope:
        if value.environment_id != "dev":
            raise ValueError("Local task profile supports only Dev")
        return value


def load_task_profile(directory: Path, settings: LocalSettings) -> LocalTaskProfile | None:
    """Validate existing state without opening writable databases or repairing it."""
    marker = directory / "human-tasks.json"
    files = (directory / "tasks.db", directory / "task-completions.db")
    if not marker.exists():
        if any(path.exists() for path in files):
            raise ValueError("Incomplete local task initialization; preserve existing state")
        return None
    with marker.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("Local task profile exceeds its size limit")
    profile = LocalTaskProfile.model_validate_json(data)
    expected = Scope(organization_id=settings.organization_id, environment_id="dev")
    if profile.installation_id != settings.installation_id or profile.scope != expected:
        raise ValueError("Local task profile does not match this installation")
    for path, tables in zip(
        files,
        (
            (
                "human_tasks",
                "human_commands",
                "human_events",
                "human_outbox",
                "human_task_forms",
                "human_task_notifications",
            ),
            ("temporal_task_completions",),
        ),
        strict=True,
    ):
        if not path.is_file():
            raise ValueError("Missing local task database; preserve existing state")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for table in tables:
                if (
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("Incomplete local task database")
        finally:
            connection.close()
    return profile


class LocalTaskAssignments:
    def __init__(self, scope: Scope) -> None:
        if scope.environment_id != "dev":
            raise ValueError("Local task assignments support only Dev")
        self.scope = scope

    def resolve(
        self, scope: Scope, user_ids: tuple[str, ...], group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        if (
            scope != self.scope
            or group_ids
            or not user_ids
            or any(user != USER_ID for user in user_ids)
        ):
            raise PermissionError("Unknown synthetic task assignment")
        return (USER_ID,)


class LocalTaskWorkerAuthorization:
    def __init__(self, scope: Scope) -> None:
        if scope.environment_id != "dev":
            raise ValueError("Local task worker supports only Dev")
        self.scope = scope

    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str:
        if scope != self.scope or permission != "task.create":
            raise PermissionError("Local task worker access denied")
        TypeAdapter(Identifier).validate_python(resource_id, strict=True)
        return "local-task-worker"


def enable_tasks(directory: Path) -> LocalTaskProfile:
    """Caller must hold the installation lock. Never restore revoked identity state."""
    settings = load_settings(directory)
    existing = load_task_profile(directory, settings)
    if existing is not None:
        return existing
    scope = Scope(organization_id=settings.organization_id, environment_id="dev")
    profile = LocalTaskProfile(installation_id=settings.installation_id, scope=scope)
    with ExitStack() as stack:
        identity = SQLiteIdentityRepository(directory / "identity.db")
        stack.callback(identity.close)
        # The fixed grant ID and zero revision reject existing revoked tombstones.
        # No membership or author permissions are recreated by this operation.
        identity.save_grant(
            Grant("local-task-participant", scope, TENANT_ID, "user", USER_ID, "task_participant"),
            expected_revision=0,
        )
        tasks = TaskService(
            directory / profile.tasks_database,
            LocalTaskWorkerAuthorization(scope),
            LocalTaskAssignments(scope),
        )
        stack.callback(tasks.close)
        journal = SQLiteCompletionJournal(directory / profile.completion_database)
        stack.callback(journal.close)
    with (directory / "human-tasks.json").open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    return profile
