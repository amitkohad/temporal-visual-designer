"""SQLite dispatch intent and exact-run acknowledgements, with bounded session counts."""

import sqlite3
from pathlib import Path
from threading import RLock

from pydantic import TypeAdapter

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.types import (
    DebugControl,
    DebugError,
    DebugRecord,
    DebugSession,
    DebugStart,
)
from nexusflow.modules.durable_runtime.types import CommandReceipt, ExecutionPlan
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.modules.project_versioning.canonical import digest


class SQLiteDebugSessionRepository:
    def __init__(self, database: str | Path, maximum_sessions_per_project: int = 1000) -> None:
        if type(maximum_sessions_per_project) is not int or maximum_sessions_per_project < 1:
            raise ValueError("Session capacity must be positive")
        self.maximum = maximum_sessions_per_project
        self.lock = RLock()
        self.connection = sqlite3.connect(str(database), check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS debug_sessions (org TEXT, env TEXT, project TEXT, "
            "operation TEXT, session_id TEXT, request TEXT NOT NULL, session TEXT NOT NULL, "
            "placement TEXT NOT NULL, plan TEXT NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(org,env,project,operation), "
            "UNIQUE(org,env,project,session_id))"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS debug_commands (org TEXT, env TEXT, project TEXT, "
            "session_id TEXT, command_id TEXT, request TEXT NOT NULL, receipt TEXT, "
            "PRIMARY KEY(org,env,project,session_id,command_id))"
        )
        self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    @staticmethod
    def _decode(row: tuple[str, ...] | None) -> DebugRecord | None:
        if row is None:
            return None
        record = DebugRecord(
            DebugStart.model_validate_json(row[0]),
            DebugSession.model_validate_json(row[1]),
            TypeAdapter(Environment).validate_json(row[2]),
            TypeAdapter(ExecutionPlan).validate_json(row[3]),
        )

        if row[4] != SQLiteDebugSessionRepository._checksum(record):
            raise DebugError("unavailable", "Debug session integrity check failed")
        session, request, plan = record.session, record.request, record.plan
        if (
            plan.binding.release_id != session.session_id
            or plan.definition_digest != session.definition_digest
            or plan.workflow_id != request.workflow_id
            or session.workflow_id != request.workflow_id
            or session.source_revision != request.source_revision
            or session.configuration_revision != request.configuration_revision
            or plan.binding.configuration_revision != request.configuration_revision
            or (plan.binding.organization_id, plan.binding.environment_id)
            != (record.placement.scope.organization_id, record.placement.scope.environment_id)
            or not plan.debug
            or plan.breakpoints != request.breakpoints
            or (session.state == "started") != bool(session.temporal_run_id)
        ):
            raise DebugError("unavailable", "Debug session binding is invalid")
        return record

    @staticmethod
    def _checksum(record: DebugRecord) -> str:
        session = record.session.model_dump(mode="json")
        return digest(
            [
                record.request.model_dump(mode="json"),
                session,
                TypeAdapter(Environment).dump_json(record.placement).decode(),
                TypeAdapter(ExecutionPlan).dump_json(record.plan).decode(),
            ]
        )

    def operation(self, scope: Scope, project_id: str, operation_id: str) -> DebugRecord | None:
        with self.lock:
            record = self._decode(
                self.connection.execute(
                    "SELECT request,session,placement,plan,checksum FROM debug_sessions "
                    "WHERE org=? AND env=? AND project=? AND operation=?",
                    (scope.organization_id, scope.environment_id, project_id, operation_id),
                ).fetchone()
            )
        if record is not None and (
            record.placement.scope != scope
            or record.session.project_id != project_id
            or record.request.operation_id != operation_id
        ):
            raise DebugError("unavailable", "Debug session scope is invalid")
        return record

    def get(self, scope: Scope, project_id: str, session_id: str) -> DebugRecord:
        with self.lock:
            record = self._decode(
                self.connection.execute(
                    "SELECT request,session,placement,plan,checksum FROM debug_sessions "
                    "WHERE org=? AND env=? AND project=? AND session_id=?",
                    (scope.organization_id, scope.environment_id, project_id, session_id),
                ).fetchone()
            )
        if record is None:
            raise DebugError("not_found", "Debug session was not found")
        if (
            record.placement.scope != scope
            or record.session.project_id != project_id
            or record.session.session_id != session_id
        ):
            raise DebugError("unavailable", "Debug session scope is invalid")
        return record

    def prepare(self, record: DebugRecord) -> DebugRecord:
        scope, project = record.placement.scope, record.session.project_id
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.operation(scope, project, record.request.operation_id)
                if existing is not None:
                    self.connection.rollback()
                    return existing
                count = self.connection.execute(
                    "SELECT count(*) FROM debug_sessions WHERE org=? AND env=? AND project=?",
                    (scope.organization_id, scope.environment_id, project),
                ).fetchone()[0]
                if count >= self.maximum:
                    raise DebugError("limit_exceeded", "Debug session capacity has been reached")
                self.connection.execute(
                    "INSERT INTO debug_sessions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        scope.organization_id,
                        scope.environment_id,
                        project,
                        record.request.operation_id,
                        record.session.session_id,
                        record.request.model_dump_json(),
                        record.session.model_dump_json(),
                        TypeAdapter(Environment).dump_json(record.placement).decode(),
                        TypeAdapter(ExecutionPlan).dump_json(record.plan).decode(),
                        self._checksum(record),
                    ),
                )
                self.connection.commit()
                return record
            except BaseException:
                self.connection.rollback()
                raise

    def acknowledge(
        self, scope: Scope, project_id: str, session_id: str, run_id: str
    ) -> DebugRecord:
        if not run_id or len(run_id) > 256:
            raise DebugError("unavailable", "Runtime returned an invalid run identifier")
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                record = self.get(scope, project_id, session_id)
                if record.session.temporal_run_id not in (None, run_id):
                    raise DebugError("conflict", "Debug session is bound to another run")
                session = record.session.model_copy(
                    update={"temporal_run_id": run_id, "state": "started"}
                )
                self.connection.execute(
                    "UPDATE debug_sessions SET session=?,checksum=? WHERE org=? AND env=? AND project=? AND session_id=?",
                    (
                        session.model_dump_json(),
                        self._checksum(
                            DebugRecord(record.request, session, record.placement, record.plan)
                        ),
                        scope.organization_id,
                        scope.environment_id,
                        project_id,
                        session_id,
                    ),
                )
                self.connection.commit()
                return DebugRecord(record.request, session, record.placement, record.plan)
            except BaseException:
                self.connection.rollback()
                raise

    def claim_command(
        self, scope: Scope, project_id: str, session_id: str, command: DebugControl
    ) -> CommandReceipt | None:
        key = (
            scope.organization_id,
            scope.environment_id,
            project_id,
            session_id,
            command.command_id,
        )
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.get(scope, project_id, session_id)
                row = self.connection.execute(
                    "SELECT request,receipt FROM debug_commands WHERE org=? AND env=? AND project=? AND session_id=? AND command_id=?",
                    key,
                ).fetchone()
                if row is not None:
                    if DebugControl.model_validate_json(row[0]) != command:
                        raise DebugError("conflict", "Debug command already has another request")
                    result = (
                        None
                        if row[1] is None
                        else TypeAdapter(CommandReceipt).validate_json(row[1])
                    )
                    if result is not None and result.command_id != command.command_id:
                        raise DebugError("unavailable", "Debug command receipt binding is invalid")
                    self.connection.commit()
                    return result
                count = self.connection.execute(
                    "SELECT count(*) FROM debug_commands WHERE org=? AND env=? AND project=? AND session_id=?",
                    key[:4],
                ).fetchone()[0]
                if count >= 1000:
                    raise DebugError("limit_exceeded", "Debug command capacity has been reached")
                self.connection.execute(
                    "INSERT INTO debug_commands VALUES(?,?,?,?,?,?,NULL)",
                    (*key, command.model_dump_json()),
                )
                self.connection.commit()
                return None
            except BaseException:
                self.connection.rollback()
                raise

    def acknowledge_command(
        self,
        scope: Scope,
        project_id: str,
        session_id: str,
        command: DebugControl,
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        if (
            receipt.command_id != command.command_id
            or type(receipt.revision) is not int
            or receipt.revision < 0
        ):
            raise DebugError("unavailable", "Debug command acknowledgement is invalid")
        key = (
            scope.organization_id,
            scope.environment_id,
            project_id,
            session_id,
            command.command_id,
        )
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.get(scope, project_id, session_id)
                row = self.connection.execute(
                    "SELECT request,receipt FROM debug_commands WHERE org=? AND env=? AND project=? AND session_id=? AND command_id=?",
                    key,
                ).fetchone()
                if row is None or DebugControl.model_validate_json(row[0]) != command:
                    raise DebugError("conflict", "Debug command reservation does not match")
                if row[1] is not None:
                    previous = TypeAdapter(CommandReceipt).validate_json(row[1])
                    if previous != receipt:
                        raise DebugError("conflict", "Debug command acknowledgement changed")
                self.connection.execute(
                    "UPDATE debug_commands SET receipt=? WHERE org=? AND env=? AND project=? AND session_id=? AND command_id=?",
                    (TypeAdapter(CommandReceipt).dump_json(receipt).decode(), *key),
                )
                self.connection.commit()
                return receipt
            except BaseException:
                self.connection.rollback()
                raise
