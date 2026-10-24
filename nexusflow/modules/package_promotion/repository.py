"""Atomic project pointer, operation receipts and audit in a local SQLite provider."""

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from pydantic import TypeAdapter

from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationEvent,
    ActivationRecord,
    ActivationRequest,
)
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import Release


class SQLiteActivationRepository:
    def __init__(self, database: str | Path) -> None:
        self._db = sqlite3.connect(str(database), check_same_thread=False, timeout=30)
        self._lock = RLock()
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS activation_operations (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                operation TEXT NOT NULL, revision INTEGER NOT NULL, request TEXT NOT NULL,
                record TEXT NOT NULL, PRIMARY KEY(org,env,project,operation),
                UNIQUE(org,env,project,revision));
            CREATE TABLE IF NOT EXISTS active_projects (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                operation TEXT NOT NULL, PRIMARY KEY(org,env,project));
            CREATE TABLE IF NOT EXISTS activation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                event TEXT NOT NULL);
        """)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _key(scope: Scope, project_id: str) -> tuple[str, ...]:
        return scope.organization_id, scope.environment_id, project_id

    def receipt(self, scope: Scope, request: ActivationRequest) -> ActivationRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT request,record FROM activation_operations
                WHERE org=? AND env=? AND project=? AND operation=?""",
                (*self._key(scope, request.project_id), request.operation_id),
            ).fetchone()
            if row is None:
                return None
            if row[0] != canonical(request.model_dump(mode="json")):
                raise ActivationError("conflict", "Operation ID already used for another request")
            return TypeAdapter(ActivationRecord).validate_json(row[1])

    def current(self, scope: Scope, project_id: str) -> ActivationRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT o.record FROM active_projects a JOIN activation_operations o
                ON a.org=o.org AND a.env=o.env AND a.project=o.project AND a.operation=o.operation
                WHERE a.org=? AND a.env=? AND a.project=?""",
                self._key(scope, project_id),
            ).fetchone()
            return None if row is None else TypeAdapter(ActivationRecord).validate_json(row[0])

    def retained(self, scope: Scope, project_id: str, release_id: str) -> Release | None:
        with self._lock:
            rows = self._db.execute(
                """SELECT record FROM activation_operations WHERE org=? AND env=? AND project=?
                ORDER BY revision DESC""",
                self._key(scope, project_id),
            )
            for row in rows:
                record = TypeAdapter(ActivationRecord).validate_json(row[0])
                if record.release.release_id == release_id:
                    return record.release
        return None

    def commit(
        self, scope: Scope, request: ActivationRequest, release: Release, actor_id: str
    ) -> ActivationRecord:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.receipt(scope, request)
                if existing is not None:
                    self._db.commit()
                    return existing
                prior = self.current(scope, request.project_id)
                revision = 0 if prior is None else prior.revision
                if revision != request.expected_revision:
                    raise ActivationError("conflict", "Activation revision has changed")
                record = ActivationRecord(
                    scope,
                    request,
                    release,
                    revision + 1,
                    None if prior is None else prior.release.release_id,
                    actor_id,
                    datetime.now(UTC).isoformat(),
                )
                self._db.execute(
                    "INSERT INTO activation_operations VALUES (?,?,?,?,?,?,?)",
                    (
                        *self._key(scope, request.project_id),
                        request.operation_id,
                        record.revision,
                        canonical(request.model_dump(mode="json")),
                        TypeAdapter(ActivationRecord).dump_json(record).decode(),
                    ),
                )
                self._db.execute(
                    """INSERT INTO active_projects VALUES (?,?,?,?) ON CONFLICT(org,env,project)
                    DO UPDATE SET operation=excluded.operation""",
                    (*self._key(scope, request.project_id), request.operation_id),
                )
                self._event(
                    scope,
                    request,
                    release.executable_digest,
                    record.previous_release_id,
                    actor_id,
                    "succeeded",
                )
                self._db.commit()
                return record
            except BaseException:
                self._db.rollback()
                raise

    def _event(
        self,
        scope: Scope,
        request: ActivationRequest,
        checksum: str,
        previous: str | None,
        actor: str,
        outcome: str,
    ) -> None:
        event = ActivationEvent(
            request.operation_id,
            request.action,
            request.release_id,
            checksum,
            previous,
            actor,
            outcome,
            datetime.now(UTC).isoformat(),
        )
        self._db.execute(
            "INSERT INTO activation_events (org,env,project,event) VALUES (?,?,?,?)",
            (*self._key(scope, request.project_id), canonical(asdict(event))),
        )

    def rejected(
        self, scope: Scope, request: ActivationRequest, actor: str, outcome: str, checksum: str = ""
    ) -> None:
        with self._lock, self._db:
            prior = self.current(scope, request.project_id)
            self._event(
                scope,
                request,
                checksum,
                None if prior is None else prior.release.release_id,
                actor,
                outcome,
            )

    def events(self, scope: Scope, project_id: str) -> tuple[ActivationEvent, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT event FROM activation_events WHERE org=? AND env=? AND project=? ORDER BY sequence",
                self._key(scope, project_id),
            )
            return tuple(TypeAdapter(ActivationEvent).validate_json(row[0]) for row in rows)
