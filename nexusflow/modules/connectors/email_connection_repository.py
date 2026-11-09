"""Scoped immutable email revisions; metadata reads never load private definitions."""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionPage,
    EmailConnectionRecord,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.types import ConnectionError
from nexusflow.modules.project_versioning.canonical import canonical, digest

_PUBLIC = ",".join(
    f"CASE WHEN typeof({name})='text' AND length(CAST({name} AS BLOB)) BETWEEN 1 AND 128 "
    f"THEN {name} ELSE NULL END AS {name}"
    for name in ("organization_id", "environment_id", "connection_id", "connection_revision")
) + (
    ",CASE WHEN typeof(revision)='integer' THEN revision ELSE NULL END AS revision,"
    "CASE WHEN typeof(metadata_json)='text' AND length(CAST(metadata_json AS BLOB)) "
    "BETWEEN 1 AND 4096 THEN metadata_json ELSE NULL END AS metadata_json,"
    "CASE WHEN typeof(metadata_checksum)='text' AND length(CAST(metadata_checksum AS BLOB))=71 "
    "THEN metadata_checksum ELSE NULL END AS metadata_checksum"
)
_PRIVATE = (
    "CASE WHEN typeof(definition_json)='text' AND length(CAST(definition_json AS BLOB)) "
    "BETWEEN 1 AND 16384 THEN definition_json ELSE NULL END AS definition_json"
)


def _identifiers(scope: Scope, *identifiers: str) -> tuple[str, ...]:
    try:
        if type(scope) is not Scope:
            raise ValueError
        values = (scope.organization_id, scope.environment_id, *identifiers)
        for value in values:
            if type(value) is not str:
                raise ValueError
            TypeAdapter(Identifier).validate_python(value, strict=True)
        return values
    except (AttributeError, TypeError, ValueError):
        raise ConnectionError("invalid_request", "Invalid connection identifier") from None


def _object(encoded: str, maximum: int) -> dict[str, Any]:
    if type(encoded) is not str or len(encoded.encode("utf-8")) > maximum:
        raise ValueError("Stored document exceeds limit")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate stored key")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise ValueError("Invalid stored constant")

    value = json.loads(encoded, object_pairs_hook=unique, parse_constant=reject)
    if type(value) is not dict:
        raise ValueError("Stored document must be an object")
    return value


class SQLiteEmailConnectionRepository:
    def __init__(
        self,
        database: str | Path,
        maximum_connections_per_scope: int = 100,
        maximum_revisions_per_connection: int = 100,
    ) -> None:
        if any(
            type(value) is not int or value < 1
            for value in (maximum_connections_per_scope, maximum_revisions_per_connection)
        ):
            raise ValueError("Connection quotas must be positive")
        self.maximum_connections_per_scope = maximum_connections_per_scope
        self.maximum_revisions_per_connection = maximum_revisions_per_connection
        self._lock = RLock()
        try:
            self._db = sqlite3.connect(database, check_same_thread=False, timeout=30)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS email_connections (
            organization_id TEXT NOT NULL, environment_id TEXT NOT NULL,
            connection_id TEXT NOT NULL, connection_revision TEXT NOT NULL,
            revision INTEGER NOT NULL, metadata_json TEXT NOT NULL,
            definition_json TEXT NOT NULL, metadata_checksum TEXT NOT NULL,
            PRIMARY KEY(organization_id,environment_id,connection_id,connection_revision),
            UNIQUE(organization_id,environment_id,connection_id,revision))""")
            self._db.commit()
        except Exception:
            if hasattr(self, "_db"):
                try:
                    self._db.close()
                except Exception:
                    pass
            raise ConnectionError(
                "unavailable", "Email connection storage is unavailable"
            ) from None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValueError,
            TypeError,
            RecursionError,
            OverflowError,
        ):
            raise ConnectionError("unavailable", "Connection storage is unavailable") from None

    @staticmethod
    def _metadata(row: sqlite3.Row) -> EmailConnectionMetadata:
        value = _object(row["metadata_json"], 4096)
        metadata = EmailConnectionMetadata.model_validate_json(canonical(value))
        encoded = canonical(metadata.model_dump(mode="json"))
        timestamp = datetime.fromisoformat(metadata.created_at)
        if (
            encoded != row["metadata_json"]
            or digest(metadata.model_dump(mode="json")) != row["metadata_checksum"]
            or (
                metadata.scope.organization_id,
                metadata.scope.environment_id,
                metadata.connection_id,
                metadata.connection_revision,
                metadata.revision,
            )
            != (
                row["organization_id"],
                row["environment_id"],
                row["connection_id"],
                row["connection_revision"],
                row["revision"],
            )
            or timestamp.utcoffset() != timedelta(0)
            or timestamp.isoformat() != metadata.created_at
        ):
            raise ConnectionError("unavailable", "Connection metadata integrity check failed")
        return metadata

    @classmethod
    def _record(cls, row: sqlite3.Row) -> EmailConnectionRecord:
        metadata = cls._metadata(row)
        value = _object(row["definition_json"], 16384)
        request = EmailConnectionWrite.model_validate_json(canonical(value))
        encoded = canonical(request.model_dump(mode="json"))
        if (
            encoded != row["definition_json"]
            or digest(request.model_dump(mode="json")) != metadata.definition_digest
            or (request.secret_reference is not None) != metadata.has_secret_reference
        ):
            raise ConnectionError("unavailable", "Connection definition integrity check failed")
        return EmailConnectionRecord(metadata, encoded)

    def put(
        self,
        scope: Scope,
        connection_id: str,
        connection_revision: str,
        request: EmailConnectionWrite,
        actor_id: str,
    ) -> EmailConnectionMetadata:
        key = _identifiers(scope, connection_id, connection_revision)
        _identifiers(scope, actor_id)
        try:
            if type(request) is not EmailConnectionWrite:
                raise ValueError
            request = EmailConnectionWrite.model_validate(
                request.model_dump(mode="python", warnings=False), strict=True
            )
            encoded = canonical(request.model_dump(mode="json"))
            if len(encoded.encode("utf-8")) > 16384:
                raise ValueError("Definition exceeds limit")
        except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise ConnectionError("invalid_request", "Invalid connection definition") from None

        def store() -> EmailConnectionMetadata:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    existing = self._db.execute(
                        f"SELECT {_PUBLIC},{_PRIVATE} FROM email_connections "
                        "WHERE organization_id=? AND environment_id=? AND connection_id=? "
                        "AND connection_revision=?",
                        key,
                    ).fetchone()
                    if existing is not None:
                        record = self._record(existing)
                        if record.definition_json != encoded:
                            raise ConnectionError("conflict", "Connection revision is immutable")
                        self._db.commit()
                        return record.metadata
                    count, highest = self._db.execute(
                        "SELECT count(*),coalesce(max(revision),0) FROM email_connections "
                        "WHERE organization_id=? AND environment_id=? AND connection_id=?",
                        key[:3],
                    ).fetchone()
                    if count >= self.maximum_revisions_per_connection:
                        raise ConnectionError("limit_exceeded", "Connection revision quota reached")
                    if not count:
                        connections = self._db.execute(
                            "SELECT count(DISTINCT connection_id) FROM email_connections "
                            "WHERE organization_id=? AND environment_id=?",
                            key[:2],
                        ).fetchone()[0]
                        if connections >= self.maximum_connections_per_scope:
                            raise ConnectionError("limit_exceeded", "Connection quota reached")
                    if type(highest) is not int or not 0 <= highest < 2**63 - 1:
                        raise ConnectionError(
                            "unavailable", "Connection revision counter is invalid"
                        )
                    metadata = EmailConnectionMetadata(
                        scope=Scope(organization_id=key[0], environment_id=key[1]),
                        connection_id=connection_id,
                        connection_revision=connection_revision,
                        revision=highest + 1,
                        definition_digest=digest(request.model_dump(mode="json")),
                        has_secret_reference=request.secret_reference is not None,
                        actor_id=actor_id,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                    self._db.execute(
                        "INSERT INTO email_connections (organization_id,environment_id,connection_id,"
                        "connection_revision,revision,metadata_json,definition_json,metadata_checksum) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            *key,
                            metadata.revision,
                            canonical(metadata.model_dump(mode="json")),
                            encoded,
                            digest(metadata.model_dump(mode="json")),
                        ),
                    )
                    self._db.commit()
                    return metadata
                except BaseException:
                    self._db.rollback()
                    raise

        return self._safe(store)

    def _read(
        self, scope: Scope, connection_id: str, connection_revision: str, *, private: bool
    ) -> sqlite3.Row:
        key = _identifiers(scope, connection_id, connection_revision)
        with self._lock:
            row = self._db.execute(
                f"SELECT {_PUBLIC}"
                + ("," + _PRIVATE if private else "")
                + " FROM email_connections WHERE organization_id=? AND environment_id=? "
                "AND connection_id=? AND connection_revision=?",
                key,
            ).fetchone()
        if row is None:
            raise ConnectionError("not_found", "Connection revision was not found")
        if not isinstance(row, sqlite3.Row):
            raise ConnectionError("unavailable", "Connection storage is unavailable")
        return row

    def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> EmailConnectionRecord:
        return self._safe(
            lambda: self._record(
                self._read(scope, connection_id, connection_revision, private=True)
            )
        )

    def metadata(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> EmailConnectionMetadata:
        return self._safe(
            lambda: self._metadata(
                self._read(scope, connection_id, connection_revision, private=False)
            )
        )

    def list(
        self,
        scope: Scope,
        connection_id: str,
        limit: int = 25,
        before_revision: int | None = None,
    ) -> EmailConnectionPage:
        key = _identifiers(scope, connection_id)
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or (
                before_revision is not None
                and (type(before_revision) is not int or not 1 <= before_revision < 2**63)
            )
        ):
            raise ConnectionError("invalid_request", "Invalid connection pagination")

        def read() -> EmailConnectionPage:
            with self._lock:
                rows = self._db.execute(
                    f"SELECT {_PUBLIC} FROM email_connections "
                    "WHERE organization_id=? AND environment_id=? AND connection_id=? "
                    "AND (typeof(revision)!='integer' OR revision<=?) "
                    "ORDER BY revision DESC LIMIT ?",
                    (
                        *key,
                        before_revision - 1 if before_revision is not None else 2**63 - 1,
                        limit + 1,
                    ),
                ).fetchall()
            checked = tuple(self._metadata(row) for row in rows)
            items = checked[:limit]
            return EmailConnectionPage(
                items=items,
                next_before_revision=items[-1].revision if len(rows) > limit else None,
            )

        return self._safe(read)
