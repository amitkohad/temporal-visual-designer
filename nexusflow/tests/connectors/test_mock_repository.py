"""Private durable fixture identity, isolation, bounded storage and recovery."""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_storage_codec import encode_scenario
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "private-fixture-payload"


def scenario(**changes):
    return replace(
        HTTPMockScenario(
            scope=SCOPE,
            connection_id="hr",
            connection_revision="r1",
            expected_path="/" + PRIVATE,
            expected_request=HTTPAttemptRequest(method="POST", body=PRIVATE.encode()),
            status_code=201,
            headers=(("x-private", PRIVATE),),
            body=PRIVATE.encode(),
        ),
        **changes,
    )


def failure(call, code):
    with pytest.raises(HTTPMockStorageError) as error:
        call()
    assert error.value.code == code
    assert PRIVATE not in str(error.value) + repr(error.value)
    assert error.value.__suppress_context__


def test_restart_digest_receipt_and_independent_snapshots(tmp_path):
    database = tmp_path / "fixtures.db"
    original = scenario()
    store = SQLiteHTTPMockScenarios(database)
    receipt = store.put(original)
    assert receipt.fixture_digest == scenario_digest(original)
    assert receipt.stored_bytes == len(encode_scenario(original).encode())
    assert PRIVATE not in repr(receipt)
    store.close()
    store = SQLiteHTTPMockScenarios(database)
    try:
        first = asyncio.run(store.get(SCOPE, "hr", "r1"))
        assert first == original and first is not original
        object.__setattr__(first.scope, "environment_id", "changed")
        second = asyncio.run(store.get(SCOPE, "hr", "r1"))
        assert second == original
        assert store.put(original) == receipt
    finally:
        store.close()


@pytest.mark.parametrize(
    "scope,id,revision",
    [
        (Scope(organization_id="other", environment_id="dev"), "hr", "r1"),
        (Scope(organization_id="acme", environment_id="uat"), "hr", "r1"),
        (SCOPE, "other", "r1"),
        (SCOPE, "hr", "other"),
    ],
)
def test_missing_scope_or_pin_never_falls_back(tmp_path, scope, id, revision):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    try:
        store.put(scenario())
        assert asyncio.run(store.get(scope, id, revision)) is None
    finally:
        store.close()


def test_scope_quotas_are_independent(tmp_path):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db", max_scenarios_per_scope=1)
    try:
        store.put(scenario())
        store.put(scenario(scope=Scope(organization_id="other", environment_id="dev")))
        store.put(scenario(scope=Scope(organization_id="acme", environment_id="uat")))
        failure(lambda: store.put(scenario(connection_revision="r2")), "limit_exceeded")
    finally:
        store.close()


def test_exact_retry_succeeds_at_count_and_byte_quota(tmp_path):
    value = scenario()
    size = len(encode_scenario(value).encode())
    store = SQLiteHTTPMockScenarios(
        tmp_path / "f.db", max_scenarios_per_scope=1, max_total_bytes_per_scope=size
    )
    try:
        first = store.put(value)
        assert store.put(value) == first
        failure(lambda: store.put(scenario(body=b"different")), "conflict")
        assert asyncio.run(store.get(SCOPE, "hr", "r1")) == value
    finally:
        store.close()


def test_byte_quota_denial_does_not_leave_row(tmp_path):
    value = scenario()
    store = SQLiteHTTPMockScenarios(
        tmp_path / "f.db", max_total_bytes_per_scope=len(encode_scenario(value).encode()) - 1
    )
    try:
        failure(lambda: store.put(value), "limit_exceeded")
        assert asyncio.run(store.get(SCOPE, "hr", "r1")) is None
        assert store.put(scenario(body=b"", headers=())).connection_id == "hr"
    finally:
        store.close()


@pytest.mark.parametrize("quota", [0, -1, True, 1.0, "1", None, 1001])
def test_invalid_count_quota(tmp_path, quota):
    with pytest.raises(ValueError, match="Invalid HTTP mock fixture quotas"):
        SQLiteHTTPMockScenarios(tmp_path / "f.db", max_scenarios_per_scope=quota)


@pytest.mark.parametrize("quota", [0, -1, True, 1.0, "1", None, 67108865])
def test_invalid_byte_quota(tmp_path, quota):
    with pytest.raises(ValueError, match="Invalid HTTP mock fixture quotas"):
        SQLiteHTTPMockScenarios(tmp_path / "f.db", max_total_bytes_per_scope=quota)


@pytest.mark.parametrize("same", [True, False])
def test_concurrent_independent_connections_are_atomic(tmp_path, same):
    database = tmp_path / "f.db"
    stores = [SQLiteHTTPMockScenarios(database, max_scenarios_per_scope=1) for _ in range(2)]
    barrier = threading.Barrier(2)

    def put(index):
        barrier.wait(timeout=5)
        try:
            return stores[index].put(scenario(connection_revision="r1" if same else f"r{index}"))
        except HTTPMockStorageError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(put, (0, 1)))
        if same:
            assert results[0] == results[1]
        else:
            assert results.count("limit_exceeded") == 1
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM http_mock_scenarios").fetchone()[0] == 1
    finally:
        for store in stores:
            store.close()


def test_concurrent_byte_quota_cannot_be_double_spent(tmp_path):
    database = tmp_path / "f.db"
    size = len(encode_scenario(scenario()).encode())
    stores = [SQLiteHTTPMockScenarios(database, max_total_bytes_per_scope=size) for _ in range(2)]
    barrier = threading.Barrier(2)

    def put(index):
        barrier.wait(timeout=5)
        try:
            return stores[index].put(scenario(connection_revision=f"r{index}"))
        except HTTPMockStorageError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(put, (0, 1)))
        assert results.count("limit_exceeded") == 1
        with sqlite3.connect(database) as db:
            assert (
                db.execute("SELECT sum(stored_bytes) FROM http_mock_scenarios").fetchone()[0]
                == size
            )
    finally:
        for store in stores:
            store.close()


def test_concurrent_different_content_claims_cannot_overwrite(tmp_path):
    database = tmp_path / "f.db"
    stores = [SQLiteHTTPMockScenarios(database) for _ in range(2)]
    barrier = threading.Barrier(2)

    def put(index):
        barrier.wait(timeout=5)
        try:
            return stores[index].put(scenario(status_code=201 + index))
        except HTTPMockStorageError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(put, (0, 1)))
        assert results.count("conflict") == 1
        winner = next(result for result in results if result != "conflict")
        assert (
            scenario_digest(asyncio.run(stores[0].get(SCOPE, "hr", "r1"))) == winner.fixture_digest
        )
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize(
    "sql,params",
    [
        ("UPDATE http_mock_scenarios SET fixture_digest=?", ("sha256:" + "0" * 64,)),
        ("UPDATE http_mock_scenarios SET fixture_digest=?", (PRIVATE * 100000,)),
        ("UPDATE http_mock_scenarios SET fixture_digest=?", (b"sha256:" + b"0" * 64,)),
        ("UPDATE http_mock_scenarios SET stored_bytes=1", ()),
        ("UPDATE http_mock_scenarios SET stored_bytes=-1", ()),
        ("UPDATE http_mock_scenarios SET definition_json=?", (PRIVATE,)),
        ("UPDATE http_mock_scenarios SET definition_json=CAST(definition_json AS BLOB)", ()),
        ("UPDATE http_mock_scenarios SET connection_revision='wrong'", ()),
    ],
)
def test_corruption_is_private_and_never_overwritten(tmp_path, sql, params):
    database = tmp_path / "f.db"
    store = SQLiteHTTPMockScenarios(database)
    try:
        store.put(scenario())
        with sqlite3.connect(database) as db:
            db.execute(sql, params)
        revision = "wrong" if "connection_revision" in sql else "r1"
        with pytest.raises(HTTPMockError) as error:
            asyncio.run(store.get(SCOPE, "hr", revision))
        assert error.value.code == "unavailable"
        assert PRIVATE not in str(error.value) + repr(error.value)
        assert error.value.__suppress_context__
        value = scenario(connection_revision=revision)
        failure(lambda: store.put(value), "unavailable")
    finally:
        store.close()


def test_corrupt_quota_accounting_fails_closed_and_recovers(tmp_path):
    database = tmp_path / "f.db"
    store = SQLiteHTTPMockScenarios(database)
    try:
        receipt = store.put(scenario())
        with sqlite3.connect(database) as db:
            db.execute("UPDATE http_mock_scenarios SET stored_bytes=0")
        failure(lambda: store.put(scenario(connection_revision="r2")), "unavailable")
        with sqlite3.connect(database) as db:
            db.execute("UPDATE http_mock_scenarios SET stored_bytes=?", (receipt.stored_bytes,))
        assert store.put(scenario(connection_revision="r2")).connection_revision == "r2"
    finally:
        store.close()


def test_oversized_stored_document_is_rejected_before_materialization(tmp_path, monkeypatch):
    database = tmp_path / "f.db"
    store = SQLiteHTTPMockScenarios(database)
    try:
        store.put(scenario())
        with sqlite3.connect(database) as db:
            db.execute(
                "UPDATE http_mock_scenarios SET definition_json=?,stored_bytes=?",
                ("x" * (12 * 1024 * 1024 + 1), 12 * 1024 * 1024 + 1),
            )

        def forbidden(_encoded):
            pytest.fail("Oversized stored document must not reach the decoder")

        monkeypatch.setattr(
            "nexusflow.modules.connectors.mock_repository.decode_scenario", forbidden
        )
        with pytest.raises(HTTPMockError) as error:
            asyncio.run(store.get(SCOPE, "hr", "r1"))
        assert error.value.code == "unavailable"
    finally:
        store.close()


@pytest.mark.parametrize(
    "scope,id,revision",
    [(None, "hr", "r1"), (SCOPE, "bad/id", "r1"), (SCOPE, "hr", 1)],
)
def test_bad_lookup_is_invalid_request(tmp_path, scope, id, revision):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    try:
        with pytest.raises(HTTPMockError) as error:
            asyncio.run(store.get(scope, id, revision))
        assert error.value.code == "invalid_request"
    finally:
        store.close()


def test_closed_store_is_private_unavailable(tmp_path):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    store.close()
    failure(lambda: store.put(scenario()), "unavailable")
    with pytest.raises(HTTPMockError) as error:
        asyncio.run(store.get(SCOPE, "hr", "r1"))
    assert error.value.code == "unavailable"


def test_invalid_write_leaves_storage_usable(tmp_path):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    try:
        failure(lambda: store.put(scenario(status_code=True)), "invalid_request")
        assert store.put(scenario()).fixture_digest == scenario_digest(scenario())
    finally:
        store.close()


def test_async_read_runs_outside_event_loop(tmp_path, monkeypatch):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    main = threading.get_ident()
    original = store._get
    threads = []

    def observed(key):
        threads.append(threading.get_ident())
        return original(key)

    monkeypatch.setattr(store, "_get", observed)
    try:
        store.put(scenario())
        assert asyncio.run(store.get(SCOPE, "hr", "r1")) == scenario()
        assert threads and threads[0] != main
    finally:
        store.close()
