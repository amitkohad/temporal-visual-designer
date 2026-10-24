"""Local SQLite metadata provider for immutable project revisions and releases."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from nexusflow.contracts.models import Scope
from nexusflow.modules.project_versioning.types import (
    ProjectRecord,
    Release,
    Revision,
    VersioningError,
)


class SQLiteProjectRepository:
    def list_projects(
        self, scope: Scope, limit: int = 50, offset: int = 0
    ) -> tuple[ProjectRecord, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT project,name,head FROM projects WHERE org=? AND env=? ORDER BY project LIMIT ? OFFSET ?",
                (scope.organization_id, scope.environment_id, limit, offset),
            )
            return tuple(ProjectRecord(scope, row[0], row[1], row[2]) for row in rows)

    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                name TEXT NOT NULL, head INTEGER NOT NULL,
                PRIMARY KEY (org, env, project));
            CREATE TABLE IF NOT EXISTS revisions (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                number INTEGER NOT NULL, parent INTEGER, content TEXT NOT NULL,
                summary TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL,
                PRIMARY KEY (org, env, project, number),
                FOREIGN KEY (org, env, project) REFERENCES projects(org, env, project));
            CREATE TABLE IF NOT EXISTS releases (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                id TEXT NOT NULL, source INTEGER NOT NULL, request_key TEXT NOT NULL,
                content TEXT NOT NULL, artifacts TEXT NOT NULL,
                content_digest TEXT NOT NULL, executable_digest TEXT NOT NULL,
                author TEXT NOT NULL, created TEXT NOT NULL,
                PRIMARY KEY (org, env, project, id),
                UNIQUE (org, env, project, request_key),
                FOREIGN KEY (org, env, project, source)
                    REFERENCES revisions(org, env, project, number));
            CREATE TABLE IF NOT EXISTS publication_tombstones (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                request_key TEXT NOT NULL,
                PRIMARY KEY (org, env, project, request_key));
            CREATE TABLE IF NOT EXISTS release_references (
                org TEXT NOT NULL, env TEXT NOT NULL, project TEXT NOT NULL,
                release TEXT NOT NULL, reference TEXT NOT NULL,
                PRIMARY KEY (org, env, project, release, reference),
                FOREIGN KEY (org, env, project, release)
                    REFERENCES releases(org, env, project, id));
        """)

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

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _key(scope: Scope, project_id: str) -> tuple[str, str, str]:
        return scope.organization_id, scope.environment_id, project_id

    @staticmethod
    def _missing() -> VersioningError:
        return VersioningError("not_found", "Project resource was not found")

    @staticmethod
    def _revision(scope: Scope, row: sqlite3.Row) -> Revision:
        return Revision(
            scope,
            row["project"],
            row["number"],
            row["parent"],
            row["content"],
            row["summary"],
            row["author"],
            row["created"],
        )

    @staticmethod
    def _release(scope: Scope, row: sqlite3.Row) -> Release:
        return Release(
            scope,
            row["project"],
            row["id"],
            row["source"],
            row["content"],
            row["artifacts"],
            row["content_digest"],
            row["executable_digest"],
            row["author"],
            row["created"],
        )

    def create(
        self,
        scope: Scope,
        project_id: str,
        name: str,
        content_json: str,
        summary: str,
        author_id: str,
    ) -> Revision:
        with self._transaction():
            if self._db.execute(
                "SELECT 1 FROM projects WHERE org=? AND env=? AND project=?",
                self._key(scope, project_id),
            ).fetchone():
                raise VersioningError("conflict", "Project already exists")
            self._db.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, 1)", (*self._key(scope, project_id), name)
            )
            return self._insert_revision(
                scope, project_id, 1, None, content_json, summary, author_id
            )

    def _insert_revision(
        self,
        scope: Scope,
        project_id: str,
        number: int,
        parent: int | None,
        content: str,
        summary: str,
        author: str,
    ) -> Revision:
        created = datetime.now(UTC).isoformat()
        self._db.execute(
            "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._key(scope, project_id), number, parent, content, summary, author, created),
        )
        return Revision(scope, project_id, number, parent, content, summary, author, created)

    def project(self, scope: Scope, project_id: str) -> ProjectRecord:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM projects WHERE org=? AND env=? AND project=?",
                self._key(scope, project_id),
            ).fetchone()
            if row is None:
                raise self._missing()
            return ProjectRecord(scope, project_id, row["name"], row["head"])

    def save(
        self,
        scope: Scope,
        project_id: str,
        expected_revision: int,
        content_json: str,
        summary: str,
        author_id: str,
    ) -> Revision:
        with self._transaction():
            current = self.project(scope, project_id).head_revision
            if current != expected_revision:
                raise VersioningError("conflict", "Project revision has changed")
            number = current + 1
            self._db.execute(
                "UPDATE projects SET head=? WHERE org=? AND env=? AND project=?",
                (number, *self._key(scope, project_id)),
            )
            return self._insert_revision(
                scope, project_id, number, current, content_json, summary, author_id
            )

    def revision(self, scope: Scope, project_id: str, number: int | None = None) -> Revision:
        with self._lock:
            if number is None:
                number = self.project(scope, project_id).head_revision
            row = self._db.execute(
                "SELECT * FROM revisions WHERE org=? AND env=? AND project=? AND number=?",
                (*self._key(scope, project_id), number),
            ).fetchone()
            if row is None:
                raise self._missing()
            return self._revision(scope, row)

    def history(self, scope: Scope, project_id: str) -> tuple[Revision, ...]:
        with self._lock:
            self.project(scope, project_id)
            rows = self._db.execute(
                "SELECT * FROM revisions WHERE org=? AND env=? AND project=? ORDER BY number",
                self._key(scope, project_id),
            ).fetchall()
            return tuple(self._revision(scope, row) for row in rows)

    def published(self, scope: Scope, project_id: str, idempotency_key: str) -> Release | None:
        with self._lock:
            self.project(scope, project_id)
            if self._db.execute(
                "SELECT 1 FROM publication_tombstones WHERE org=? AND env=? AND project=? AND request_key=?",
                (*self._key(scope, project_id), idempotency_key),
            ).fetchone():
                raise VersioningError("conflict", "Publication key belongs to a deleted release")
            row = self._db.execute(
                "SELECT * FROM releases WHERE org=? AND env=? AND project=? AND request_key=?",
                (*self._key(scope, project_id), idempotency_key),
            ).fetchone()
            return None if row is None else self._release(scope, row)

    def publish(
        self,
        scope: Scope,
        project_id: str,
        source_revision: int,
        idempotency_key: str,
        artifacts_json: str,
        content_digest: str,
        executable_digest: str,
        author_id: str,
    ) -> Release:
        with self._transaction():
            existing = self.published(scope, project_id, idempotency_key)
            if existing is not None:
                if existing.source_revision != source_revision:
                    raise VersioningError("conflict", "Publication key has already been used")
                return existing
            revision = self.revision(scope, project_id, source_revision)
            release_id = str(uuid4())
            created = datetime.now(UTC).isoformat()
            self._db.execute(
                "INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *self._key(scope, project_id),
                    release_id,
                    source_revision,
                    idempotency_key,
                    revision.content_json,
                    artifacts_json,
                    content_digest,
                    executable_digest,
                    author_id,
                    created,
                ),
            )
            return self.release(scope, project_id, release_id)

    def release(self, scope: Scope, project_id: str, release_id: str) -> Release:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM releases WHERE org=? AND env=? AND project=? AND id=?",
                (*self._key(scope, project_id), release_id),
            ).fetchone()
            if row is None:
                raise self._missing()
            return self._release(scope, row)

    def retain(self, scope: Scope, project_id: str, release_id: str, reference_id: str) -> None:
        with self._transaction():
            self.release(scope, project_id, release_id)
            self._db.execute(
                "INSERT OR IGNORE INTO release_references VALUES (?, ?, ?, ?, ?)",
                (*self._key(scope, project_id), release_id, reference_id),
            )

    def release_reference(
        self, scope: Scope, project_id: str, release_id: str, reference_id: str
    ) -> None:
        with self._transaction():
            self.release(scope, project_id, release_id)
            self._db.execute(
                "DELETE FROM release_references WHERE org=? AND env=? AND project=? AND release=? AND reference=?",
                (*self._key(scope, project_id), release_id, reference_id),
            )

    def delete_release(self, scope: Scope, project_id: str, release_id: str) -> None:
        with self._transaction():
            self.release(scope, project_id, release_id)
            retained = self._db.execute(
                "SELECT 1 FROM release_references WHERE org=? AND env=? AND project=? AND release=?",
                (*self._key(scope, project_id), release_id),
            ).fetchone()
            if retained:
                raise VersioningError(
                    "referenced", "Release is retained by a deployment or execution"
                )
            self._db.execute(
                "INSERT INTO publication_tombstones SELECT org, env, project, request_key FROM releases WHERE org=? AND env=? AND project=? AND id=?",
                (*self._key(scope, project_id), release_id),
            )
            self._db.execute(
                "DELETE FROM releases WHERE org=? AND env=? AND project=? AND id=?",
                (*self._key(scope, project_id), release_id),
            )
