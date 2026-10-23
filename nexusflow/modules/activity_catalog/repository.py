"""Local durable intake provider; each validated bundle commits atomically."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.types import (
    ActivityManifest,
    CatalogError,
    ImportedBundle,
    ValidatedBundle,
)


class SQLiteIntakeRepository:
    def __init__(self, database: str | Path) -> None:
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = RLock()
        self._db.execute("""CREATE TABLE IF NOT EXISTS activity_intake (
            org TEXT NOT NULL, env TEXT NOT NULL, digest TEXT NOT NULL,
            activity TEXT NOT NULL, version TEXT NOT NULL,
            manifest TEXT NOT NULL, archive BLOB NOT NULL, author TEXT NOT NULL,
            created TEXT NOT NULL, PRIMARY KEY(org, env, digest),
            UNIQUE(org, env, activity, version))""")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _record(scope: Scope, row: sqlite3.Row) -> ImportedBundle:
        return ImportedBundle(scope, row["digest"], row["manifest"], row["author"], row["created"])

    def put(self, scope: Scope, bundle: ValidatedBundle, author_id: str) -> ImportedBundle:
        # This internal provider consumes only a validated service-boundary snapshot.
        manifest = ActivityManifest.model_validate_json(bundle.manifest_json)
        ref = manifest.descriptor.activity_ref
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM activity_intake WHERE org=? AND env=? AND activity=? AND version=?",
                    (scope.organization_id, scope.environment_id, ref.activity_id, ref.version),
                ).fetchone()
                if row is not None:
                    if row["digest"] != bundle.digest:
                        raise CatalogError(
                            "conflict", "Activity version already identifies different content"
                        )
                    result = self._record(scope, row)
                else:
                    result = self._insert(scope, bundle, author_id)
                self._db.commit()
                return result
            except BaseException:
                self._db.rollback()
                raise

    def _insert(self, scope: Scope, bundle: ValidatedBundle, author_id: str) -> ImportedBundle:
        manifest = ActivityManifest.model_validate_json(bundle.manifest_json)
        ref = manifest.descriptor.activity_ref
        created = datetime.now(UTC).isoformat()
        self._db.execute(
            "INSERT INTO activity_intake VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope.organization_id,
                scope.environment_id,
                bundle.digest,
                ref.activity_id,
                ref.version,
                bundle.manifest_json,
                bundle.archive,
                author_id,
                created,
            ),
        )
        return self.get(scope, bundle.digest)

    def get(self, scope: Scope, digest: str) -> ImportedBundle:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM activity_intake WHERE org=? AND env=? AND digest=?",
                (scope.organization_id, scope.environment_id, digest),
            ).fetchone()
            if row is None:
                raise CatalogError("not_found", "Activity import was not found")
            return self._record(scope, row)

    def archive(self, scope: Scope, digest: str) -> bytes:
        with self._lock:
            row = self._db.execute(
                "SELECT archive FROM activity_intake WHERE org=? AND env=? AND digest=?",
                (scope.organization_id, scope.environment_id, digest),
            ).fetchone()
            if row is None:
                raise CatalogError("not_found", "Activity import was not found")
            return bytes(row["archive"])
