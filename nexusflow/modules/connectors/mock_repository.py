"""Immutable private SQLite fixtures for explicitly composed Dev mock workers."""

import asyncio
import sqlite3
from pathlib import Path
from threading import RLock

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_storage_codec import decode_scenario, encode_scenario
from nexusflow.modules.connectors.mock_storage_types import (
    HTTPMockFixtureReceipt,
    HTTPMockStorageError,
)
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockScenario

_MAX_DOCUMENT = 12 * 1024 * 1024
_KEY_WHERE = "organization_id=? AND environment_id=? AND connection_id=? AND connection_revision=?"


def _key(scope: Scope, connection_id: str, revision: str) -> tuple[str, str, str, str]:
    if type(scope) is not Scope:
        raise ValueError
    values = (scope.organization_id, scope.environment_id, connection_id, revision)
    for value in values:
        if type(value) is not str:
            raise ValueError
        TypeAdapter(Identifier).validate_python(value, strict=True)
    return values


class SQLiteHTTPMockScenarios:
    """Trusted-host writes; runtime reads still require the executor's fresh policy.

    Scope placement does not establish Dev eligibility. The database contains raw
    fixture data and must remain private to the host. No default wiring is added.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        max_scenarios_per_scope: int = 100,
        max_total_bytes_per_scope: int = 8388608,
    ) -> None:
        if (
            type(max_scenarios_per_scope) is not int
            or not 1 <= max_scenarios_per_scope <= 1000
            or type(max_total_bytes_per_scope) is not int
            or not 1 <= max_total_bytes_per_scope <= 67108864
        ):
            raise ValueError("Invalid HTTP mock fixture quotas") from None
        self.max_scenarios_per_scope = max_scenarios_per_scope
        self.max_total_bytes_per_scope = max_total_bytes_per_scope
        self._lock = RLock()
        try:
            self._db = sqlite3.connect(database, check_same_thread=False, timeout=5)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS http_mock_scenarios (
                organization_id TEXT NOT NULL, environment_id TEXT NOT NULL,
                connection_id TEXT NOT NULL, connection_revision TEXT NOT NULL,
                fixture_digest TEXT NOT NULL, stored_bytes INTEGER NOT NULL,
                definition_json TEXT NOT NULL,
                PRIMARY KEY(organization_id,environment_id,connection_id,connection_revision))""")
            self._db.commit()
        except Exception:
            if hasattr(self, "_db"):
                self._db.close()
            raise HTTPMockStorageError("unavailable") from None

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                raise HTTPMockStorageError("unavailable") from None

    def _record(
        self, key: tuple[str, str, str, str]
    ) -> tuple[HTTPMockScenario, HTTPMockFixtureReceipt, str] | None:
        # Read byte lengths before materializing private JSON; the surrounding
        # transaction gives these two queries the same database snapshot.
        row = self._db.execute(
            "SELECT stored_bytes,length(CAST(definition_json AS BLOB)) AS actual_bytes,"
            "typeof(definition_json) AS definition_type,typeof(fixture_digest) AS digest_type,"
            "length(CAST(fixture_digest AS BLOB)) AS digest_bytes "
            "FROM http_mock_scenarios WHERE " + _KEY_WHERE,
            key,
        ).fetchone()
        if row is None:
            return None
        if (
            type(row["stored_bytes"]) is not int
            or type(row["actual_bytes"]) is not int
            or not 1 <= row["stored_bytes"] <= _MAX_DOCUMENT
            or row["stored_bytes"] != row["actual_bytes"]
            or row["definition_type"] != "text"
            or row["digest_type"] != "text"
            or row["digest_bytes"] != 71
        ):
            raise HTTPMockStorageError("unavailable") from None
        payload = self._db.execute(
            "SELECT definition_json,fixture_digest FROM http_mock_scenarios WHERE " + _KEY_WHERE,
            key,
        ).fetchone()
        if payload is None or type(payload["definition_json"]) is not str:
            raise HTTPMockStorageError("unavailable") from None
        encoded = payload["definition_json"]
        value = decode_scenario(encoded)
        if (
            _key(value.scope, value.connection_id, value.connection_revision) != key
            or type(payload["fixture_digest"]) is not str
            or payload["fixture_digest"] != scenario_digest(value)
            or len(encoded.encode("utf-8")) != row["stored_bytes"]
        ):
            raise HTTPMockStorageError("unavailable") from None
        receipt = HTTPMockFixtureReceipt(
            value.scope.model_copy(deep=True),
            key[2],
            key[3],
            payload["fixture_digest"],
            row["stored_bytes"],
        )
        return value, receipt, encoded

    def put(self, value: HTTPMockScenario) -> HTTPMockFixtureReceipt:
        try:
            encoded = encode_scenario(value)
            snapshot = decode_scenario(encoded)
            key = _key(snapshot.scope, snapshot.connection_id, snapshot.connection_revision)
            size = len(encoded.encode("utf-8"))
            fixture_digest = scenario_digest(snapshot)
        except HTTPMockStorageError:
            raise
        except Exception:
            raise HTTPMockStorageError("invalid_request") from None
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                existing = self._record(key)
                if existing is not None:
                    if existing[2] != encoded:
                        raise HTTPMockStorageError("conflict") from None
                    self._db.commit()
                    return existing[1]
                rows = self._db.execute(
                    "SELECT stored_bytes,length(CAST(definition_json AS BLOB)) AS actual_bytes "
                    "FROM http_mock_scenarios WHERE organization_id=? AND environment_id=? "
                    "LIMIT 1001",
                    key[:2],
                ).fetchall()
                total = 0
                for row in rows:
                    if (
                        type(row["stored_bytes"]) is not int
                        or type(row["actual_bytes"]) is not int
                        or not 1 <= row["stored_bytes"] <= _MAX_DOCUMENT
                        or row["stored_bytes"] != row["actual_bytes"]
                    ):
                        raise HTTPMockStorageError("unavailable") from None
                    total += row["stored_bytes"]
                if (
                    len(rows) >= self.max_scenarios_per_scope
                    or total + size > self.max_total_bytes_per_scope
                ):
                    raise HTTPMockStorageError("limit_exceeded") from None
                self._db.execute(
                    "INSERT INTO http_mock_scenarios VALUES(?,?,?,?,?,?,?)",
                    (*key, fixture_digest, size, encoded),
                )
                self._db.commit()
                return HTTPMockFixtureReceipt(snapshot.scope, key[2], key[3], fixture_digest, size)
            except Exception as error:
                try:
                    self._db.rollback()
                except Exception:
                    pass
                if isinstance(error, HTTPMockStorageError):
                    raise HTTPMockStorageError(error.code) from None
                raise HTTPMockStorageError("unavailable") from None

    def _get(self, key: tuple[str, str, str, str]) -> HTTPMockScenario | None:
        with self._lock:
            try:
                self._db.execute("BEGIN")
                record = self._record(key)
                self._db.commit()
                return None if record is None else record[0]
            except Exception:
                try:
                    self._db.rollback()
                except Exception:
                    pass
                raise HTTPMockError("unavailable") from None

    async def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> HTTPMockScenario | None:
        try:
            key = _key(scope, connection_id, connection_revision)
        except Exception:
            raise HTTPMockError("invalid_request") from None
        return await asyncio.to_thread(self._get, key)
