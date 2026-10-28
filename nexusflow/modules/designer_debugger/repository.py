"""SQLite dispatch intent and exact-run acknowledgements, with bounded session counts."""

import sqlite3
from pathlib import Path
from threading import RLock

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.designer_debugger.listing import (
    decode_cursor,
    encode_cursor,
    validate_context,
    validate_created_at,
)
from nexusflow.modules.designer_debugger.listing_types import DebugSessionPage
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
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS debug_reruns (org TEXT, env TEXT, project TEXT, "
            "operation_id TEXT, parent_session_id TEXT NOT NULL, checksum TEXT NOT NULL, "
            "PRIMARY KEY(org,env,project,operation_id))"
        )
        self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def list_sessions(
        self,
        scope: Scope,
        project_id: str,
        actor_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DebugSessionPage:
        """Select public metadata only; authorization belongs to the caller."""
        try:
            validate_context(scope, project_id, actor_id)
        except ValueError:
            raise DebugError("invalid_request", "Invalid debug session context") from None
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DebugError("invalid_request", "Invalid debug session page limit")
        before = decode_cursor(scope, project_id, actor_id, cursor)
        clause = ""
        parameters: tuple[object, ...] = (
            scope.organization_id,
            scope.environment_id,
            project_id,
            actor_id,
        )
        if before is not None:
            clause = " AND (json_extract(session,'$.created_at')<? OR (json_extract(session,'$.created_at')=? AND session_id<?))"
            parameters += (before[0], before[0], before[1])
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT session_id,session FROM debug_sessions WHERE org=? AND env=? "
                    "AND project=? AND json_extract(session,'$.actor_id')=?"
                    + clause
                    + " ORDER BY json_extract(session,'$.created_at') DESC,session_id DESC LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
            sessions: list[DebugSession] = []
            for session_id, metadata in rows:
                session = DebugSession.model_validate_json(metadata)
                validate_created_at(session.created_at)
                if (
                    session.session_id != session_id
                    or session.project_id != project_id
                    or session.actor_id != actor_id
                    or not 1 <= session.source_revision < 2**63
                    or (session.state == "started") != bool(session.temporal_run_id)
                    or (
                        session.temporal_run_id is not None
                        and (
                            session.state != "started"
                            or not 1 <= len(session.temporal_run_id) <= 256
                        )
                    )
                ):
                    raise ValueError("Invalid metadata binding")
                sessions.append(session)
            items = tuple(sessions[:limit])
            return DebugSessionPage(
                items=items,
                next_cursor=(
                    encode_cursor(scope, project_id, actor_id, items[-1])
                    if len(sessions) > limit
                    else None
                ),
            )
        except (sqlite3.Error, ValueError, TypeError, UnicodeError, RecursionError):
            raise DebugError("unavailable", "Debug session discovery is unavailable") from None

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

    @staticmethod
    def _origin_checksum(record: DebugRecord, parent_session_id: str) -> str:
        return digest(
            [
                record.placement.scope.organization_id,
                record.placement.scope.environment_id,
                record.session.project_id,
                record.request.operation_id,
                parent_session_id,
            ]
        )

    def _parent(self, record: DebugRecord, parent_session_id: str) -> None:
        try:
            TypeAdapter(Identifier).validate_python(parent_session_id, strict=True)
            parent = self.get(record.placement.scope, record.session.project_id, parent_session_id)
        except (ValueError, DebugError):
            raise DebugError("unavailable", "Debug rerun parent is unavailable") from None
        if (
            parent.session.state != "started"
            or parent.session.temporal_run_id is None
            or parent.session.actor_id != record.session.actor_id
            or parent.session.workflow_id != record.session.workflow_id
            or parent.placement != record.placement
            or parent.request.operation_id == record.request.operation_id
        ):
            raise DebugError("unavailable", "Debug rerun parent binding is invalid")

    def assert_origin(self, record: DebugRecord, parent_session_id: str | None = None) -> None:
        """Validate saved lineage before a retry can dispatch or return a receipt."""
        scope = record.placement.scope
        with self.lock:
            row = self.connection.execute(
                "SELECT parent_session_id,checksum FROM debug_reruns "
                "WHERE org=? AND env=? AND project=? AND operation_id=?",
                (
                    scope.organization_id,
                    scope.environment_id,
                    record.session.project_id,
                    record.request.operation_id,
                ),
            ).fetchone()
            if row is not None:
                if row[1] != self._origin_checksum(record, row[0]):
                    raise DebugError("unavailable", "Debug rerun integrity check failed")
                self._parent(record, row[0])
            if (None if row is None else row[0]) != parent_session_id:
                raise DebugError("conflict", "Debug operation already has another origin")

    def prepare(self, record: DebugRecord, parent_session_id: str | None = None) -> DebugRecord:
        scope, project = record.placement.scope, record.session.project_id
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.operation(scope, project, record.request.operation_id)
                if existing is not None:
                    self.assert_origin(existing, parent_session_id)
                    self.connection.rollback()
                    return existing
                orphan = self.connection.execute(
                    "SELECT 1 FROM debug_reruns WHERE org=? AND env=? AND project=? AND operation_id=?",
                    (
                        scope.organization_id,
                        scope.environment_id,
                        project,
                        record.request.operation_id,
                    ),
                ).fetchone()
                if orphan is not None:
                    raise DebugError("unavailable", "Debug rerun intent is unavailable")
                if parent_session_id is not None:
                    self._parent(record, parent_session_id)
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
                if parent_session_id is not None:
                    self.connection.execute(
                        "INSERT INTO debug_reruns VALUES(?,?,?,?,?,?)",
                        (
                            scope.organization_id,
                            scope.environment_id,
                            project,
                            record.request.operation_id,
                            parent_session_id,
                            self._origin_checksum(record, parent_session_id),
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
