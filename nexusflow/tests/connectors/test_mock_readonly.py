"""Existing fixture stores can be consumed without schema or fixture writes."""

import asyncio
import sqlite3
from dataclasses import replace

import pytest

from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_schema import validate_http_mock_schema
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockError
from nexusflow.tests.connectors.test_mock_repository import PRIVATE, SCOPE, scenario


def unavailable(operation):
    with pytest.raises(HTTPMockStorageError) as caught:
        operation()
    assert caught.value.code == "unavailable"
    assert str(caught.value) == "HTTP mock fixture storage failed"
    assert PRIVATE not in repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.fixture
def saved(tmp_path):
    database = tmp_path / "fixtures.db"
    writer = SQLiteHTTPMockScenarios(database)
    receipt = writer.put(scenario())
    yield database, writer, receipt
    writer.close()


def test_readonly_get_and_listing_preserve_existing_pins(saved):
    database, _, receipt = saved
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    try:
        assert asyncio.run(reader.get(SCOPE, "hr", "r1")) == scenario()
        assert reader.list_metadata(SCOPE).items == (receipt,)
        assert asyncio.run(reader.get(SCOPE, "hr", "missing")) is None
        other = SCOPE.model_copy(update={"organization_id": "other"})
        assert asyncio.run(reader.get(other, "hr", "r1")) is None
        assert reader.list_metadata(other).items == ()
    finally:
        reader.close()


def test_readonly_put_refuses_before_private_input_is_encoded(saved, monkeypatch):
    database, writer, receipt = saved
    reader = SQLiteHTTPMockScenarios(database, read_only=True)

    def forbidden(_value):
        pytest.fail("Read-only writes must not inspect or encode fixture content")

    monkeypatch.setattr("nexusflow.modules.connectors.mock_repository.encode_scenario", forbidden)
    try:
        unavailable(lambda: reader.put(scenario()))
        unavailable(lambda: reader.put(None))
        assert writer.list_metadata(SCOPE).items == (receipt,)
    finally:
        reader.close()


def test_sqlite_itself_enforces_readonly_access(saved):
    database, writer, receipt = saved
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._db.execute("DELETE FROM http_mock_scenarios")
        reader._db.rollback()
        assert writer.list_metadata(SCOPE).items == (receipt,)
    finally:
        reader.close()


def test_worker_reads_see_subsequent_committed_admin_revisions(saved):
    database, writer, receipt = saved
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    second = replace(scenario(), connection_revision="r2", status_code=202)
    try:
        assert asyncio.run(reader.get(SCOPE, "hr", "r1")) == scenario()
        second_receipt = writer.put(second)
        assert asyncio.run(reader.get(SCOPE, "hr", "r2")) == second
        assert reader.list_metadata(SCOPE).items == (receipt, second_receipt)
    finally:
        reader.close()


def test_readonly_open_and_queries_execute_no_mutating_sql(saved, monkeypatch):
    database, _, _ = saved
    statements = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    try:
        asyncio.run(reader.get(SCOPE, "hr", "r1"))
        reader.list_metadata(SCOPE)
    finally:
        reader.close()
    normalized = [statement.strip().upper() for statement in statements]
    assert normalized
    assert all(
        statement.startswith(("SELECT", "PRAGMA TABLE_INFO", "BEGIN", "COMMIT"))
        for statement in normalized
    )
    assert not any(
        "JOURNAL_MODE" in statement or "CREATE TABLE" in statement for statement in normalized
    )


def test_readonly_open_does_not_change_existing_delete_journal_mode(tmp_path):
    database = tmp_path / "fixtures.db"
    writer = SQLiteHTTPMockScenarios(database)
    writer.put(scenario())
    writer.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    try:
        assert asyncio.run(reader.get(SCOPE, "hr", "r1")) == scenario()
    finally:
        reader.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize("read_only", [None, 0, 1, "true", 1.0])
def test_access_mode_is_a_native_boolean_before_open(tmp_path, read_only):
    database = tmp_path / "fixtures.db"
    with pytest.raises(ValueError, match="access mode"):
        SQLiteHTTPMockScenarios(database, read_only=read_only)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["missing", "directory", "corrupt", "memory"])
def test_readonly_never_creates_or_repairs_invalid_paths(tmp_path, kind):
    database = tmp_path / "fixtures.db"
    if kind == "directory":
        database.mkdir()
    elif kind == "corrupt":
        database.write_bytes(PRIVATE.encode())
    target = ":memory:" if kind == "memory" else database
    before = database.read_bytes() if kind == "corrupt" else None
    unavailable(lambda: SQLiteHTTPMockScenarios(target, read_only=True))
    if kind == "missing":
        assert not database.exists()
    if kind == "corrupt":
        assert database.read_bytes() == before


_SCHEMA = """CREATE TABLE http_mock_scenarios (
organization_id TEXT NOT NULL, environment_id TEXT NOT NULL,
connection_id TEXT NOT NULL, connection_revision TEXT NOT NULL,
fixture_digest TEXT NOT NULL, stored_bytes INTEGER NOT NULL,
definition_json TEXT NOT NULL,
PRIMARY KEY(organization_id,environment_id,connection_id,connection_revision))"""


@pytest.mark.parametrize(
    "damage", ["missing", "extra", "nullable", "type", "primary_key", "order", "view"]
)
def test_incompatible_schema_is_rejected_without_creation_or_migration(tmp_path, damage):
    database = tmp_path / "fixtures.db"
    with sqlite3.connect(database) as connection:
        if damage == "view":
            connection.execute("CREATE VIEW http_mock_scenarios AS SELECT 1 AS organization_id")
        elif damage != "missing":
            schema = _SCHEMA
            if damage == "extra":
                schema = schema.replace(
                    "definition_json TEXT NOT NULL,", "definition_json TEXT NOT NULL, extra TEXT,"
                )
            if damage == "nullable":
                schema = schema.replace("fixture_digest TEXT NOT NULL", "fixture_digest TEXT")
            if damage == "type":
                schema = schema.replace("stored_bytes INTEGER", "stored_bytes TEXT")
            if damage == "primary_key":
                schema = schema.replace(
                    "PRIMARY KEY(organization_id,environment_id",
                    "PRIMARY KEY(environment_id,organization_id",
                )
            if damage == "order":
                schema = schema.replace(
                    "fixture_digest TEXT NOT NULL, stored_bytes INTEGER NOT NULL,",
                    "stored_bytes INTEGER NOT NULL, fixture_digest TEXT NOT NULL,",
                )
            connection.execute(schema)
    before = database.read_bytes()
    unavailable(lambda: SQLiteHTTPMockScenarios(database, read_only=True))
    assert database.read_bytes() == before


def test_effective_wal_schema_drop_is_not_repaired(saved):
    database, writer, _ = saved
    writer._db.execute("PRAGMA wal_autocheckpoint=0")
    writer._db.execute("DROP TABLE http_mock_scenarios")
    writer._db.commit()
    wal = database.with_name(database.name + "-wal")
    before = database.read_bytes(), wal.read_bytes()
    unavailable(lambda: SQLiteHTTPMockScenarios(database, read_only=True))
    assert (database.read_bytes(), wal.read_bytes()) == before
    assert (
        writer._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='http_mock_scenarios'"
        ).fetchone()
        is None
    )


def test_valid_wal_only_schema_and_data_are_visible(tmp_path):
    database = tmp_path / "fixtures.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute(_SCHEMA)
        connection.commit()
        reader = SQLiteHTTPMockScenarios(database, read_only=True)
        try:
            assert reader.list_metadata(SCOPE).items == ()
        finally:
            reader.close()
    finally:
        connection.close()


def test_shared_schema_validator_is_private_on_closed_connection():
    connection = sqlite3.connect(":memory:")
    connection.close()
    with pytest.raises(ValueError) as caught:
        validate_http_mock_schema(connection)
    assert str(caught.value) == "Invalid HTTP mock fixture schema"
    assert caught.value.__suppress_context__


def test_shared_schema_validation_never_reads_fixture_rows(saved):
    _, writer, _ = saved
    reads = []

    def authorize(action, table, column, database, source):
        if action == sqlite3.SQLITE_READ:
            reads.append(table)
            if table == "http_mock_scenarios":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    writer._db.set_authorizer(authorize)
    try:
        validate_http_mock_schema(writer._db)
        assert "http_mock_scenarios" not in reads
    finally:
        writer._db.set_authorizer(None)


def test_failed_readonly_schema_validation_closes_the_opened_connection(tmp_path, monkeypatch):
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()
    connect = sqlite3.connect
    closed = []

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()

    def tracked(*args, **kwargs):
        return connect(*args, **kwargs, factory=TrackedConnection)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    unavailable(lambda: SQLiteHTTPMockScenarios(database, read_only=True))
    assert len(closed) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        closed[0].execute("SELECT 1")


def test_closed_reader_errors_are_private_and_admin_remains_usable(saved):
    database, writer, receipt = saved
    reader = SQLiteHTTPMockScenarios(database, read_only=True)
    reader.close()
    unavailable(lambda: reader.put(scenario()))
    unavailable(lambda: reader.list_metadata(SCOPE))
    with pytest.raises(HTTPMockError) as caught:
        asyncio.run(reader.get(SCOPE, "hr", "r1"))
    assert caught.value.code == "unavailable"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert writer.put(scenario()) == receipt


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_readonly_refuses_symlinked_store_files(tmp_path, suffix):
    database = tmp_path / "fixtures.db"
    writer = SQLiteHTTPMockScenarios(database)
    writer.close()
    selected = database.with_name(database.name + suffix)
    target = tmp_path / "external.db"
    target.write_bytes(PRIVATE.encode())
    if selected.exists():
        selected.unlink()
    try:
        selected.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit unprivileged symlink creation")
    unavailable(lambda: SQLiteHTTPMockScenarios(database, read_only=True))
    assert target.read_bytes() == PRIVATE.encode()
