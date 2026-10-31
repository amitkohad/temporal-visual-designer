"""Private-store enumeration is bounded metadata, never fixture verification."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockError
from nexusflow.tests.connectors.test_mock_repository import PRIVATE, SCOPE, scenario


@pytest.fixture
def store(tmp_path):
    result = SQLiteHTTPMockScenarios(tmp_path / "fixtures.db")
    yield result
    result.close()


def keys(page):
    return [(item.connection_id, item.connection_revision) for item in page.items]


def fail(call, code):
    with pytest.raises(HTTPMockStorageError) as caught:
        call()
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


def test_empty_and_lexical_exclusive_pages(store):
    assert keys(store.list_metadata(SCOPE)) == []
    expected = [("A", "r1"), ("a", "r1"), ("a", "r10"), ("a", "r2"), ("z", "r1")]
    for connection, revision in reversed(expected):
        store.put(scenario(connection_id=connection, connection_revision=revision))
    first = store.list_metadata(SCOPE, limit=2)
    assert keys(first) == expected[:2]
    assert (first.next_after_connection_id, first.next_after_connection_revision) == expected[1]
    second = store.list_metadata(
        SCOPE,
        limit=2,
        after_connection_id=first.next_after_connection_id,
        after_connection_revision=first.next_after_connection_revision,
    )
    assert keys(second) == expected[2:4]
    final = store.list_metadata(
        SCOPE,
        limit=2,
        after_connection_id=second.next_after_connection_id,
        after_connection_revision=second.next_after_connection_revision,
    )
    assert keys(final) == expected[4:]
    assert final.next_after_connection_id is final.next_after_connection_revision is None


def test_exact_limit_does_not_invent_cursor_and_sparse_cursor(store):
    store.put(scenario(connection_id="a"))
    store.put(scenario(connection_id="z"))
    page = store.list_metadata(SCOPE, limit=2)
    assert page.next_after_connection_id is page.next_after_connection_revision is None
    assert keys(
        store.list_metadata(SCOPE, after_connection_id="missing", after_connection_revision="r5")
    ) == [("z", "r1")]
    assert (
        store.list_metadata(SCOPE, after_connection_id="zz", after_connection_revision="r1").items
        == ()
    )


def test_scope_isolation_restart_receipt_identity_and_snapshots(tmp_path):
    path = tmp_path / "f.db"
    first = SQLiteHTTPMockScenarios(path)
    receipt = first.put(scenario())
    assert first.put(scenario()) == receipt
    foreign = Scope(organization_id="other", environment_id="dev")
    first.put(scenario(scope=foreign))
    first.put(scenario(scope=SCOPE.model_copy(update={"environment_id": "uat"})))
    first.close()
    reopened = SQLiteHTTPMockScenarios(path)
    try:
        page = reopened.list_metadata(SCOPE)
        assert page.items == (receipt,)
        assert PRIVATE not in repr(page)
        object.__setattr__(page.items[0].scope, "organization_id", "mutated")
        assert reopened.list_metadata(SCOPE).items[0].scope == SCOPE
        assert len(reopened.list_metadata(foreign).items) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("limit", [0, 51, -1, True, 1.0, "1", None])
def test_bad_limits_fail_before_sql(store, limit):
    store.close()
    fail(lambda: store.list_metadata(SCOPE, limit=limit), "invalid_request")


@pytest.mark.parametrize(
    "cursor",
    [
        (None, "r1"),
        ("a", None),
        ("", "r1"),
        ("a", ""),
        ("private/" + PRIVATE, "r1"),
        ("a", 1),
        (True, "r1"),
        ("a" * 129, "r1"),
    ],
)
def test_bad_cursor_is_private_invalid_request(store, cursor):
    fail(
        lambda: store.list_metadata(
            SCOPE, after_connection_id=cursor[0], after_connection_revision=cursor[1]
        ),
        "invalid_request",
    )


@pytest.mark.parametrize(
    "scope",
    [
        None,
        {"organization_id": "acme", "environment_id": "dev"},
        Scope.model_construct(organization_id=1, environment_id="dev"),
    ],
)
def test_bad_scope(store, scope):
    fail(lambda: store.list_metadata(scope), "invalid_request")


def test_limit_fifty_and_lookahead(store):
    for index in range(51):
        store.put(scenario(connection_id=f"c{index:03}"))
    page = store.list_metadata(SCOPE, limit=50)
    assert len(page.items) == 50
    assert page.next_after_connection_id == "c049"
    assert keys(
        store.list_metadata(SCOPE, after_connection_id="c049", after_connection_revision="r1")
    ) == [("c050", "r1")]


@pytest.mark.parametrize(
    "field,value",
    [
        ("fixture_digest", "sha256:" + "z" * 64),
        ("fixture_digest", "x" * 100000),
        ("fixture_digest", sqlite3.Binary(b"x" * 71)),
        ("connection_id", "x" * 100000),
        ("connection_id", "bad/key"),
        ("connection_revision", sqlite3.Binary(b"r1")),
        ("stored_bytes", "x" * 100000),
        ("stored_bytes", -1),
        ("stored_bytes", 12582913),
        ("definition_json", sqlite3.Binary(b"private")),
        ("definition_json", "bad document size"),
    ],
    ids=[
        "digest_format",
        "digest_large",
        "digest_blob",
        "key_large",
        "key_bad",
        "revision_blob",
        "bytes_text",
        "bytes_negative",
        "bytes_large",
        "document_blob",
        "document_size",
    ],
)
def test_corrupt_metadata_fails_closed(store, field, value):
    store.put(scenario())
    store._db.execute(f"UPDATE http_mock_scenarios SET {field}=?", (value,))
    store._db.commit()
    fail(lambda: store.list_metadata(SCOPE), "unavailable")


def test_corrupt_lookahead_is_not_an_unsafe_cursor(store):
    store.put(scenario(connection_id="a"))
    store.put(scenario(connection_id="z"))
    store._db.execute(
        "UPDATE http_mock_scenarios SET fixture_digest=? WHERE connection_id='z'", ("bad",)
    )
    store._db.commit()
    fail(lambda: store.list_metadata(SCOPE, limit=1), "unavailable")


def test_listing_never_decodes_or_fetches_private_document(store, monkeypatch):
    receipt = store.put(scenario())
    forbidden = Mock(side_effect=AssertionError("Private document decoding"))
    monkeypatch.setattr("nexusflow.modules.connectors.mock_repository.decode_scenario", forbidden)

    def metadata_only_text(value):
        decoded = value.decode("utf-8")
        assert PRIVATE not in decoded
        return decoded

    store._db.text_factory = metadata_only_text
    queries = []
    store._db.set_trace_callback(queries.append)
    assert store.list_metadata(SCOPE).items == (receipt,)
    forbidden.assert_not_called()
    assert len(queries) == 1
    assert "SELECT definition_json" not in queries[0]
    assert PRIVATE not in repr(queries)


def test_same_length_private_corruption_is_not_claimed_verified(store):
    store.put(scenario())
    store._db.execute(
        "UPDATE http_mock_scenarios SET definition_json=replace(definition_json,?,?)",
        (PRIVATE, "x" * len(PRIVATE)),
    )
    store._db.commit()
    assert len(store.list_metadata(SCOPE).items) == 1
    with pytest.raises(HTTPMockError):
        asyncio.run(store.get(SCOPE, "hr", "r1"))


def test_closed_store_returns_private_unavailable(store):
    store.close()
    fail(lambda: store.list_metadata(SCOPE), "unavailable")


def test_independent_writers_can_continue_after_exclusive_cursor(tmp_path):
    path = tmp_path / "f.db"
    first, second = SQLiteHTTPMockScenarios(path), SQLiteHTTPMockScenarios(path)
    try:
        first.put(scenario(connection_id="a"))
        first.put(scenario(connection_id="m"))
        page = first.list_metadata(SCOPE, limit=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(second.put, scenario(connection_id=key)) for key in ("b", "z")]
            for future in futures:
                future.result()
        assert keys(
            first.list_metadata(
                SCOPE,
                after_connection_id=page.next_after_connection_id,
                after_connection_revision=page.next_after_connection_revision,
            )
        ) == [("b", "r1"), ("m", "r1"), ("z", "r1")]
    finally:
        first.close()
        second.close()
