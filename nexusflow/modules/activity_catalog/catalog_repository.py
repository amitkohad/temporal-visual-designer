"""Transactional local catalog provider with durable immutable version reservations."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from nexusflow.contracts.models import ActivityRef, Scope
from nexusflow.modules.activity_catalog.catalog_types import (
    CatalogEntry,
    CatalogEvent,
    ExportedActivity,
)
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError, ImportedBundle

_METADATA = "org,env,activity,version,digest,manifest,state,generation,publisher,published,updated_by,updated,deleted"


class SQLiteCatalogRepository:
    def __init__(self, database: str | Path) -> None:
        self._db = sqlite3.connect(str(database), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.create_function("casefold", 1, str.casefold, deterministic=True)
        self._lock = RLock()
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS activity_catalog (
                org TEXT NOT NULL, env TEXT NOT NULL, activity TEXT NOT NULL,
                version TEXT NOT NULL, digest TEXT NOT NULL, manifest TEXT NOT NULL,
                archive BLOB, state TEXT NOT NULL, generation INTEGER NOT NULL,
                publisher TEXT NOT NULL, published TEXT NOT NULL,
                updated_by TEXT NOT NULL, updated TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(org,env,activity,version), UNIQUE(org,env,digest));
            CREATE TABLE IF NOT EXISTS activity_catalog_usage (
                org TEXT NOT NULL, env TEXT NOT NULL, activity TEXT NOT NULL,
                version TEXT NOT NULL, digest TEXT NOT NULL, usage TEXT NOT NULL,
                PRIMARY KEY(org,env,activity,version,digest,usage));
            CREATE TABLE IF NOT EXISTS activity_catalog_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                org TEXT NOT NULL, env TEXT NOT NULL, activity TEXT NOT NULL,
                version TEXT NOT NULL, digest TEXT NOT NULL, action TEXT NOT NULL,
                actor TEXT NOT NULL, timestamp TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS activity_catalog_scope_state
                ON activity_catalog(org,env,deleted,state,activity,version);
        """)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

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

    @staticmethod
    def _key(scope: Scope, reference: ActivityRef) -> tuple[str, ...]:
        return (
            scope.organization_id,
            scope.environment_id,
            reference.activity_id,
            reference.version,
            reference.artifact_digest,
        )

    @staticmethod
    def _record(scope: Scope, row: sqlite3.Row) -> CatalogEntry:
        return CatalogEntry(
            scope,
            ActivityRef(
                activity_id=row["activity"], version=row["version"], artifact_digest=row["digest"]
            ),
            row["manifest"],
            row["state"],
            row["generation"],
            row["publisher"],
            row["published"],
            row["updated_by"],
            row["updated"],
        )

    def _row(
        self, scope: Scope, reference: ActivityRef, *, include_archive: bool = False
    ) -> sqlite3.Row:
        columns = _METADATA + (",archive" if include_archive else "")
        row = self._db.execute(
            f"SELECT {columns} FROM activity_catalog WHERE org=? AND env=? AND activity=? AND version=? AND digest=? AND deleted=0",
            self._key(scope, reference),
        ).fetchone()
        if row is None:
            raise CatalogError("not_found", "Activity version was not found")
        assert isinstance(row, sqlite3.Row)
        return row

    def _event(
        self, scope: Scope, reference: ActivityRef, action: str, actor_id: str, timestamp: str
    ) -> None:
        self._db.execute(
            "INSERT INTO activity_catalog_events (org,env,activity,version,digest,action,actor,timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (*self._key(scope, reference), action, actor_id, timestamp),
        )

    def by_digest(self, scope: Scope, digest: str) -> CatalogEntry | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_METADATA} FROM activity_catalog WHERE org=? AND env=? AND digest=? AND deleted=0",
                (scope.organization_id, scope.environment_id, digest),
            ).fetchone()
            return None if row is None else self._record(scope, row)

    def publish(self, imported: ImportedBundle, archive: bytes, actor_id: str) -> CatalogEntry:
        manifest = ActivityManifest.model_validate_json(imported.manifest_json)
        reference = manifest.descriptor.activity_ref
        if reference.artifact_digest != imported.digest:
            raise CatalogError("conflict", "Bundle reference does not match imported digest")
        with self._transaction():
            row = self._db.execute(
                f"SELECT {_METADATA} FROM activity_catalog WHERE org=? AND env=? AND activity=? AND version=?",
                self._key(imported.scope, reference)[:4],
            ).fetchone()
            if row is not None:
                if row["digest"] != imported.digest or row["deleted"]:
                    raise CatalogError("conflict", "Activity version is already reserved")
                return self._record(imported.scope, row)
            timestamp = datetime.now(UTC).isoformat()
            self._db.execute(
                "INSERT INTO activity_catalog VALUES (?,?,?,?,?,?,?,'published',1,?,?,?,?,0)",
                (
                    *self._key(imported.scope, reference),
                    imported.manifest_json,
                    archive,
                    actor_id,
                    timestamp,
                    actor_id,
                    timestamp,
                ),
            )
            self._event(imported.scope, reference, "published", actor_id, timestamp)
            return self._record(imported.scope, self._row(imported.scope, reference))

    def get(self, scope: Scope, reference: ActivityRef) -> CatalogEntry:
        with self._lock:
            return self._record(scope, self._row(scope, reference))

    def search(
        self,
        scope: Scope,
        query: str = "",
        category: str | None = None,
        include_deprecated: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CatalogEntry, ...]:
        if limit < 1 or limit > 100 or offset < 0:
            raise CatalogError("invalid_request", "Invalid pagination")
        with self._lock:
            rows = self._db.execute(
                f"""SELECT {_METADATA} FROM activity_catalog
                    WHERE org=? AND env=? AND deleted=0
                    AND (state='published' OR (? AND state='deprecated'))
                    AND (? IS NULL OR json_extract(manifest,'$.descriptor.category')=?)
                    AND instr(casefold(activity || ' ' ||
                        json_extract(manifest,'$.descriptor.display_name') || ' ' ||
                        json_extract(manifest,'$.descriptor.description')),?) > 0
                    ORDER BY activity,version LIMIT ? OFFSET ?""",
                (
                    scope.organization_id,
                    scope.environment_id,
                    include_deprecated,
                    category,
                    category,
                    query.casefold(),
                    limit,
                    offset,
                ),
            ).fetchall()
            return tuple(self._record(scope, row) for row in rows)

    def transition(
        self,
        scope: Scope,
        reference: ActivityRef,
        target: Literal["deprecated", "withdrawn"],
        expected_generation: int,
        actor_id: str,
    ) -> CatalogEntry:
        with self._transaction():
            row = self._row(scope, reference)
            if row["state"] == target:
                return self._record(scope, row)
            if row["generation"] != expected_generation:
                raise CatalogError("conflict", "Activity version changed")
            if target not in ("deprecated", "withdrawn") or row["state"] == "withdrawn":
                raise CatalogError("invalid_transition", "Activity lifecycle cannot move backwards")
            timestamp = datetime.now(UTC).isoformat()
            self._db.execute(
                "UPDATE activity_catalog SET state=?,generation=generation+1,updated_by=?,updated=? WHERE org=? AND env=? AND activity=? AND version=? AND digest=?",
                (target, actor_id, timestamp, *self._key(scope, reference)),
            )
            self._event(scope, reference, target, actor_id, timestamp)
            return self._record(scope, self._row(scope, reference))

    def _held(self, scope: Scope, reference: ActivityRef, usage_id: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM activity_catalog_usage WHERE org=? AND env=? AND activity=? AND version=? AND digest=? AND usage=?",
                (*self._key(scope, reference), usage_id),
            ).fetchone()
            is not None
        )

    def retain(self, scope: Scope, reference: ActivityRef, usage_id: str) -> None:
        if not usage_id:
            raise CatalogError("invalid_request", "Usage identifier is required")
        with self._transaction():
            row = self._row(scope, reference)
            if row["state"] == "withdrawn" and not self._held(scope, reference, usage_id):
                raise CatalogError("withdrawn", "Activity version is withdrawn")
            self._db.execute(
                "INSERT OR IGNORE INTO activity_catalog_usage VALUES (?,?,?,?,?,?)",
                (*self._key(scope, reference), usage_id),
            )

    def release_reference(self, scope: Scope, reference: ActivityRef, usage_id: str) -> None:
        with self._transaction():
            self._db.execute(
                "DELETE FROM activity_catalog_usage WHERE org=? AND env=? AND activity=? AND version=? AND digest=? AND usage=?",
                (*self._key(scope, reference), usage_id),
            )

    def export(
        self, scope: Scope, reference: ActivityRef, usage_id: str | None = None
    ) -> ExportedActivity:
        with self._lock:
            row = self._row(scope, reference, include_archive=True)
            held = usage_id is not None and self._held(scope, reference, usage_id)
            if usage_id is not None and not held:
                raise CatalogError("forbidden", "Usage reference was not found")
            if row["state"] == "withdrawn" and not held:
                raise CatalogError("withdrawn", "Activity version is withdrawn")
            return ExportedActivity(reference, row["manifest"], bytes(row["archive"]))

    def delete(self, scope: Scope, reference: ActivityRef, actor_id: str) -> None:
        with self._transaction():
            row = self._row(scope, reference)
            if row["state"] != "withdrawn":
                raise CatalogError("invalid_transition", "Only withdrawn versions can be deleted")
            if (
                self._db.execute(
                    "SELECT 1 FROM activity_catalog_usage WHERE org=? AND env=? AND activity=? AND version=? AND digest=? LIMIT 1",
                    self._key(scope, reference),
                ).fetchone()
                is not None
            ):
                raise CatalogError("referenced", "Activity version is still referenced")
            timestamp = datetime.now(UTC).isoformat()
            self._db.execute(
                "UPDATE activity_catalog SET deleted=1,archive=NULL WHERE org=? AND env=? AND activity=? AND version=? AND digest=?",
                self._key(scope, reference),
            )
            self._event(scope, reference, "deleted", actor_id, timestamp)

    def events(self, scope: Scope, reference: ActivityRef) -> tuple[CatalogEvent, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM activity_catalog_events WHERE org=? AND env=? AND activity=? AND version=? AND digest=? ORDER BY sequence",
                self._key(scope, reference),
            ).fetchall()
            return tuple(
                CatalogEvent(reference, row["action"], row["actor"], row["timestamp"])
                for row in rows
            )
