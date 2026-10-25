"""Immutable destination intake; staged archives are not executable releases."""

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.package_promotion.package_format import (
    PackageArchive,
    PackageLimits,
    PackageManifest,
    decode_package,
)
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.canonical import canonical, digest
from nexusflow.modules.project_versioning.types import ProjectAuthorization


class PackageImportMetadata(Contract):
    operation_id: Identifier
    revision: Annotated[int, Field(ge=1, lt=2**63)]
    project_id: Identifier
    release_id: Identifier
    package_digest: Digest
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    actor_id: str
    created_at: str
    state: Literal["staged"] = "staged"


class PackageImportPage(Contract):
    items: tuple[PackageImportMetadata, ...]
    next_before_revision: int | None


@dataclass(frozen=True)
class PackageImportDownload:
    metadata: PackageImportMetadata
    archive: bytes


class PackageImportRepository(Protocol):
    def put(
        self, scope: Scope, operation_id: str, package: PackageArchive, actor_id: str
    ) -> PackageImportMetadata: ...
    def list(
        self, scope: Scope, limit: int = 50, before_revision: int | None = None
    ) -> PackageImportPage: ...
    def get(self, scope: Scope, operation_id: str) -> PackageImportMetadata: ...
    def manifest(self, scope: Scope, operation_id: str) -> PackageManifest: ...
    def download(self, scope: Scope, operation_id: str) -> PackageImportDownload: ...


_COLUMNS = "operation_id, revision, project_id, release_id, package_digest, source_revision, actor_id, created_at, state"


class SQLitePackageImportRepository:
    def __init__(self, database: str | Path, *, maximum_imports_per_scope: int = 100) -> None:
        if type(maximum_imports_per_scope) is not int or maximum_imports_per_scope < 1:
            raise ValueError("Import quota must be a positive integer")
        self.maximum_imports_per_scope = maximum_imports_per_scope
        self._lock = RLock()
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS package_imports (
            organization_id TEXT NOT NULL, environment_id TEXT NOT NULL,
            operation_id TEXT NOT NULL, revision INTEGER NOT NULL,
            project_id TEXT NOT NULL, release_id TEXT NOT NULL,
            package_digest TEXT NOT NULL, source_revision INTEGER NOT NULL,
            actor_id TEXT NOT NULL, created_at TEXT NOT NULL, state TEXT NOT NULL,
            manifest TEXT NOT NULL, archive BLOB NOT NULL,
            physical_sha TEXT NOT NULL, physical_size INTEGER NOT NULL,
            PRIMARY KEY (organization_id, environment_id, operation_id),
            UNIQUE (organization_id, environment_id, revision))""")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _scope(scope: Scope) -> tuple[str, str]:
        return scope.organization_id, scope.environment_id

    @staticmethod
    def _metadata(row: sqlite3.Row) -> PackageImportMetadata:
        try:
            return PackageImportMetadata.model_validate(
                {key: row[key] for key in _COLUMNS.split(", ")}
            )
        except ValidationError:
            raise ActivationError("unavailable", "Import storage is unavailable") from None

    def put(
        self, scope: Scope, operation_id: str, package: PackageArchive, actor_id: str
    ) -> PackageImportMetadata:
        if package.manifest.organization_id != scope.organization_id:
            raise ActivationError("forbidden", "Package organization does not match destination")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute(
                    f"SELECT {_COLUMNS} FROM package_imports WHERE organization_id=? AND environment_id=? AND operation_id=?",
                    (*self._scope(scope), operation_id),
                ).fetchone()
                if existing is not None:
                    result = self._metadata(existing)
                    if result.package_digest != package.manifest.package_digest:
                        raise ActivationError("conflict", "Import operation already exists")
                    self._db.commit()
                    return result
                row = self._db.execute(
                    "SELECT COUNT(*), COALESCE(MAX(revision),0) FROM package_imports WHERE organization_id=? AND environment_id=?",
                    self._scope(scope),
                ).fetchone()
                if row[0] >= self.maximum_imports_per_scope:
                    raise ActivationError("import_limit", "Import quota reached")
                manifest = package.manifest
                result = PackageImportMetadata(
                    operation_id=operation_id,
                    revision=row[1] + 1,
                    project_id=manifest.project_id,
                    release_id=manifest.release_id,
                    package_digest=manifest.package_digest,
                    source_revision=manifest.source_revision,
                    actor_id=actor_id,
                    created_at=datetime.now(UTC).isoformat(),
                )
                self._db.execute(
                    "INSERT INTO package_imports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *self._scope(scope),
                        *[getattr(result, key) for key in _COLUMNS.split(", ")],
                        canonical(manifest.model_dump(mode="json")),
                        package.archive,
                        hashlib.sha256(package.archive).hexdigest(),
                        len(package.archive),
                    ),
                )
                self._db.commit()
                return result
            except BaseException:
                self._db.rollback()
                raise

    def list(
        self, scope: Scope, limit: int = 50, before_revision: int | None = None
    ) -> PackageImportPage:
        with self._lock:
            cursor_clause = " AND revision<?" if before_revision is not None else ""
            parameters = (
                (*self._scope(scope), before_revision, limit + 1)
                if before_revision is not None
                else (*self._scope(scope), limit + 1)
            )
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM package_imports WHERE organization_id=? AND environment_id=?{cursor_clause} ORDER BY revision DESC LIMIT ?",
                parameters,
            ).fetchall()
        items = tuple(self._metadata(row) for row in rows[:limit])
        return PackageImportPage(
            items=items, next_before_revision=items[-1].revision if len(rows) > limit else None
        )

    def _row(self, scope: Scope, operation_id: str, columns: str) -> sqlite3.Row:
        row = self._db.execute(
            f"SELECT {columns} FROM package_imports WHERE organization_id=? AND environment_id=? AND operation_id=?",
            (*self._scope(scope), operation_id),
        ).fetchone()
        if row is None:
            raise ActivationError("not_found", "Import operation was not found")
        if not isinstance(row, sqlite3.Row):
            raise ActivationError("unavailable", "Import storage is unavailable")
        return row

    def get(self, scope: Scope, operation_id: str) -> PackageImportMetadata:
        with self._lock:
            return self._metadata(self._row(scope, operation_id, _COLUMNS))

    def manifest(self, scope: Scope, operation_id: str) -> PackageManifest:
        with self._lock:
            row = self._row(scope, operation_id, _COLUMNS + ", manifest")
            metadata = self._metadata(row)
            try:
                manifest = PackageManifest.model_validate_json(row["manifest"])
            except ValidationError:
                raise ActivationError("unavailable", "Import storage is unavailable") from None
            if (
                manifest.organization_id != scope.organization_id
                or manifest.package_digest
                != digest(manifest.model_dump(mode="json", exclude={"package_digest"}))
                or (
                    manifest.project_id,
                    manifest.release_id,
                    manifest.package_digest,
                    manifest.source_revision,
                )
                != (
                    metadata.project_id,
                    metadata.release_id,
                    metadata.package_digest,
                    metadata.source_revision,
                )
            ):
                raise ActivationError("unavailable", "Import storage is unavailable")
            return manifest

    def download(self, scope: Scope, operation_id: str) -> PackageImportDownload:
        with self._lock:
            row = self._row(
                scope, operation_id, _COLUMNS + ", archive, physical_sha, physical_size"
            )
            archive = row["archive"]
            if (
                type(archive) is not bytes
                or len(archive) != row["physical_size"]
                or hashlib.sha256(archive).hexdigest() != row["physical_sha"]
            ):
                raise ActivationError("unavailable", "Import storage is unavailable")
            self.manifest(scope, operation_id)
            return PackageImportDownload(self._metadata(row), archive)


class PackageImportService:
    def __init__(
        self,
        repository: PackageImportRepository,
        authorization: ProjectAuthorization,
        limits: PackageLimits | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.limits = limits or PackageLimits()

    def _authorize(self, scope: Scope, operation_id: str | None = None) -> str:
        actor = self.authorization.authorize(scope, "package.import", "package-imports")
        if operation_id is not None:
            try:
                TypeAdapter(Identifier).validate_python(operation_id, strict=True)
            except ValidationError:
                raise ActivationError("invalid_request", "Invalid import identifier") from None
        return actor

    def stage(self, scope: Scope, operation_id: str, archive: bytes) -> PackageImportMetadata:
        actor = self._authorize(scope, operation_id)
        package = decode_package(archive, self.limits)
        if package.manifest.organization_id != scope.organization_id:
            raise ActivationError("forbidden", "Package organization does not match destination")
        return self._safe(lambda: self.repository.put(scope, operation_id, package, actor))

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError, TypeError):
            raise ActivationError("unavailable", "Import storage is unavailable") from None

    def list(
        self, scope: Scope, *, limit: int = 50, before_revision: int | None = None
    ) -> PackageImportPage:
        self._authorize(scope)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (
                before_revision is not None
                and (type(before_revision) is not int or not 1 <= before_revision < 2**63)
            )
        ):
            raise ActivationError("invalid_request", "Invalid import pagination")
        return self._safe(lambda: self.repository.list(scope, limit, before_revision))

    def get(self, scope: Scope, operation_id: str) -> PackageImportMetadata:
        self._authorize(scope, operation_id)
        return self._safe(lambda: self.repository.get(scope, operation_id))

    def manifest(self, scope: Scope, operation_id: str) -> PackageManifest:
        self._authorize(scope, operation_id)
        return self._safe(lambda: self.repository.manifest(scope, operation_id))

    def download(self, scope: Scope, operation_id: str) -> PackageImportDownload:
        self._authorize(scope, operation_id)
        return self._safe(lambda: self.repository.download(scope, operation_id))
