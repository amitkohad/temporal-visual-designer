"""Durable public configuration revisions, independent of secrets and activation."""

import json
import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import DataSchema, Identifier, Scope, check_data_schema
from nexusflow.modules.package_promotion.configuration_types import (
    ConfigurationMetadata,
    ConfigurationPage,
    ConfigurationRecord,
    ConfigurationRepository,
    ConfigurationWrite,
)
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.canonical import canonical, digest
from nexusflow.modules.project_versioning.types import ProjectAuthorization


def _bounded(value: Any, *, references: bool = False) -> str:
    entries = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 16:
            raise ValueError("Configuration nesting exceeds limit")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("Configuration keys must be strings")
            if references and ("$ref" in item or "$dynamicRef" in item):
                raise ValueError("Schema references are unsupported")
            entries += len(item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            entries += len(item)
            stack.extend((child, depth + 1) for child in item)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Invalid JSON value")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("Nonfinite JSON value")
        if entries > 1000:
            raise ValueError("Configuration entries exceed limit")
    encoded = canonical(value)
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("Configuration exceeds byte limit")
    return encoded


def _values(encoded: str) -> tuple[str, str]:
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("Configuration exceeds byte limit")
    value = json.loads(encoded)
    if type(value) is not dict:
        raise ValueError("Configuration must be an object")
    normalized = _bounded(value)
    return normalized, digest(value)


class SQLiteConfigurationRepository:
    def __init__(self, database: str | Path, *, maximum_revisions_per_project: int = 1000):
        if type(maximum_revisions_per_project) is not int or maximum_revisions_per_project < 1:
            raise ValueError("Configuration quota must be positive")
        self.maximum_revisions_per_project = maximum_revisions_per_project
        self._lock = RLock()
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS configurations (
            organization_id TEXT NOT NULL, environment_id TEXT NOT NULL,
            project_id TEXT NOT NULL, configuration_revision TEXT NOT NULL,
            revision INTEGER NOT NULL, values_json TEXT NOT NULL,
            values_digest TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, environment_id, project_id, configuration_revision),
            UNIQUE (organization_id, environment_id, project_id, revision))""")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _metadata(row: sqlite3.Row) -> ConfigurationMetadata:
        return ConfigurationMetadata.model_validate(
            {
                key: row[key]
                for key in (
                    "project_id",
                    "configuration_revision",
                    "revision",
                    "values_digest",
                    "actor_id",
                    "created_at",
                )
            }
        )

    @staticmethod
    def _key(scope: Scope, project_id: str) -> tuple[str, str, str]:
        return scope.organization_id, scope.environment_id, project_id

    def _get(self, scope: Scope, project_id: str, revision: str) -> ConfigurationRecord:
        row = self._db.execute(
            """SELECT * FROM configurations WHERE organization_id=?
            AND environment_id=? AND project_id=? AND configuration_revision=?""",
            (*self._key(scope, project_id), revision),
        ).fetchone()
        if row is None:
            raise ActivationError("not_found", "Configuration revision was not found")
        metadata = self._metadata(row)
        normalized, checksum = _values(row["values_json"])
        if normalized != row["values_json"] or checksum != metadata.values_digest:
            raise ValueError("Configuration integrity mismatch")
        return ConfigurationRecord(metadata, normalized)

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (sqlite3.Error, ValueError, TypeError, RecursionError, OverflowError):
            raise OSError("Configuration storage is unavailable") from None

    def get(
        self, scope: Scope, project_id: str, configuration_revision: str
    ) -> ConfigurationRecord:
        with self._lock:
            return self._safe(lambda: self._get(scope, project_id, configuration_revision))

    def put(
        self,
        scope: Scope,
        project_id: str,
        configuration_revision: str,
        values_json: str,
        actor_id: str,
    ) -> ConfigurationMetadata:
        try:
            normalized, checksum = _values(values_json)
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise ActivationError("invalid_request", "Invalid configuration values") from None
        with self._lock:
            return self._safe(
                lambda: self._put(
                    scope, project_id, configuration_revision, normalized, checksum, actor_id
                )
            )

    def _put(
        self,
        scope: Scope,
        project_id: str,
        configuration_revision: str,
        normalized: str,
        checksum: str,
        actor_id: str,
    ) -> ConfigurationMetadata:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            try:
                existing = self._get(scope, project_id, configuration_revision)
            except ActivationError as error:
                if error.code != "not_found":
                    raise
            else:
                if existing.values_json != normalized:
                    raise ActivationError("conflict", "Configuration revision is immutable")
                self._db.commit()
                return existing.metadata
            count, maximum = self._db.execute(
                """SELECT COUNT(*), COALESCE(MAX(revision), 0)
                FROM configurations WHERE organization_id=? AND environment_id=? AND project_id=?""",
                self._key(scope, project_id),
            ).fetchone()
            if count >= self.maximum_revisions_per_project or maximum >= 2**63 - 1:
                raise ActivationError("conflict", "Configuration revision quota exceeded")
            metadata = ConfigurationMetadata(
                project_id=project_id,
                configuration_revision=configuration_revision,
                revision=maximum + 1,
                values_digest=checksum,
                actor_id=actor_id,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._db.execute(
                "INSERT INTO configurations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *self._key(scope, project_id),
                    configuration_revision,
                    metadata.revision,
                    normalized,
                    checksum,
                    actor_id,
                    metadata.created_at,
                ),
            )
            self._db.commit()
            return metadata
        except BaseException:
            self._db.rollback()
            raise

    def list(
        self, scope: Scope, project_id: str, limit: int = 50, before_revision: int | None = None
    ) -> ConfigurationPage:
        _pagination(limit, before_revision)

        def read() -> ConfigurationPage:
            rows = self._db.execute(
                """SELECT project_id, configuration_revision, revision,
                values_digest, actor_id, created_at FROM configurations
                WHERE organization_id=? AND environment_id=? AND project_id=? AND revision<=?
                ORDER BY revision DESC LIMIT ?""",
                (
                    *self._key(scope, project_id),
                    before_revision - 1 if before_revision is not None else 2**63 - 1,
                    limit + 1,
                ),
            ).fetchall()
            items = tuple(self._metadata(row) for row in rows[:limit])
            return ConfigurationPage(
                items=items, next_before_revision=items[-1].revision if len(rows) > limit else None
            )

        with self._lock:
            return self._safe(read)

    def supports(self, scope: Scope, project_id: str, revision: str, schema: DataSchema) -> bool:
        try:
            record = self.get(scope, project_id, revision)
        except ActivationError as error:
            if error.code == "not_found":
                return False
            raise
        try:
            _bounded(schema, references=True)
            check_data_schema(schema)
            return Draft202012Validator(schema).is_valid(json.loads(record.values_json))
        except (ValueError, TypeError, RecursionError):
            return False


def _pagination(limit: int, before: int | None) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or (before is not None and (type(before) is not int or not 1 <= before < 2**63))
    ):
        raise ActivationError("invalid_request", "Invalid configuration pagination")


class ConfigurationService:
    def __init__(
        self, repository: ConfigurationRepository, authorization: ProjectAuthorization
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def _authorize(self, scope: Scope, project_id: str, revision: str | None = None) -> str:
        actor = self.authorization.authorize(scope, "connection.manage", "configurations")
        try:
            for identifier in (project_id, revision):
                if identifier is not None:
                    TypeAdapter(Identifier).validate_python(identifier, strict=True)
        except ValidationError:
            raise ActivationError("invalid_request", "Invalid configuration identifier") from None
        return actor

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (OSError, RuntimeError, sqlite3.Error, ValueError, TypeError, RecursionError):
            raise ActivationError("unavailable", "Configuration storage is unavailable") from None

    def put(
        self, scope: Scope, project_id: str, revision: str, request: ConfigurationWrite
    ) -> ConfigurationMetadata:
        actor = self._authorize(scope, project_id, revision)
        try:
            encoded = _bounded(request.values)
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise ActivationError("invalid_request", "Invalid configuration values") from None
        return self._safe(lambda: self.repository.put(scope, project_id, revision, encoded, actor))

    def get(self, scope: Scope, project_id: str, revision: str) -> ConfigurationMetadata:
        self._authorize(scope, project_id, revision)
        return self._safe(lambda: self.repository.get(scope, project_id, revision).metadata)

    def list(
        self, scope: Scope, project_id: str, *, limit: int = 50, before_revision: int | None = None
    ) -> ConfigurationPage:
        self._authorize(scope, project_id)
        _pagination(limit, before_revision)
        return self._safe(lambda: self.repository.list(scope, project_id, limit, before_revision))
