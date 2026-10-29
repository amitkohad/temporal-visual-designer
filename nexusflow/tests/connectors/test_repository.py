"""Immutable connection history, scoped storage and metadata-only read acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.types import ConnectionError, HTTPConnectionWrite
from nexusflow.modules.project_versioning.canonical import canonical, digest

SCOPE = Scope(organization_id="acme", environment_id="dev")


def definition(reference="private-reference", host="api.example.com"):
    return HTTPConnectionWrite(
        base_origin="https://" + host,
        allowed_origins=("https://" + host,),
        secret_reference=reference,
    )


@pytest.fixture
def repository(tmp_path):
    repo = SQLiteConnectionRepository(tmp_path / "connections.db")
    yield repo
    repo.close()


def test_immutable_retry_restart_and_private_definition(tmp_path):
    path = tmp_path / "connections.db"
    repo = SQLiteConnectionRepository(path)
    first = repo.put(SCOPE, "payroll", "v1", definition(), "alice")
    normalized = HTTPConnectionWrite(
        base_origin="HTTPS://API.EXAMPLE.COM:443/",
        allowed_origins=("https://api.example.com",),
        secret_reference="private-reference",
    )
    assert repo.put(SCOPE, "payroll", "v1", normalized, "bob") == first
    record = repo.get(SCOPE, "payroll", "v1")
    assert record.metadata == first
    assert json.loads(record.definition_json)["secret_reference"] == "private-reference"
    assert first.definition_digest == digest(normalized.model_dump(mode="json"))
    assert first.has_secret_reference is True
    assert "private-reference" not in first.model_dump_json()
    repo.close()
    reopened = SQLiteConnectionRepository(path)
    try:
        assert reopened.metadata(SCOPE, "payroll", "v1") == first
        assert reopened.put(SCOPE, "payroll", "v1", normalized, "charlie") == first
        assert reopened.get(SCOPE, "payroll", "v1") == record
        with pytest.raises(ConnectionError) as error:
            reopened.put(SCOPE, "payroll", "v1", definition(reference="changed"), "charlie")
        assert error.value.code == "conflict"
        assert reopened.metadata(SCOPE, "payroll", "v1") == first
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "other",
    [
        Scope(organization_id="other", environment_id="dev"),
        Scope(organization_id="acme", environment_id="prod"),
    ],
)
def test_scope_isolation_for_all_reads_and_same_names(repository, other):
    first = repository.put(SCOPE, "payroll", "v1", definition(), "alice")
    for lookup in (repository.get, repository.metadata):
        with pytest.raises(ConnectionError) as error:
            lookup(other, "payroll", "v1")
        assert error.value.code == "not_found"
    assert repository.list(other, "payroll").items == ()
    separate = repository.put(other, "payroll", "v1", definition(reference=None), "bob")
    assert separate.scope == other and separate.revision == 1
    assert separate.has_secret_reference is False
    assert repository.metadata(SCOPE, "payroll", "v1") == first


def test_ordered_exclusive_revision_pages_and_unknown_empty(repository):
    originals = [
        repository.put(SCOPE, "payroll", f"v{i}", definition(), "alice") for i in range(1, 5)
    ]
    page = repository.list(SCOPE, "payroll", limit=2)
    assert page.items == tuple(reversed(originals[2:]))
    assert page.next_before_revision == 3
    second = repository.list(SCOPE, "payroll", limit=2, before_revision=3)
    assert second.items == tuple(reversed(originals[:2]))
    assert second.next_before_revision is None
    assert repository.list(SCOPE, "payroll", before_revision=1).items == ()
    assert repository.list(SCOPE, "unknown").items == ()


def test_metadata_and_list_sql_never_read_private_definition(repository):
    repository.put(SCOPE, "payroll", "v1", definition(), "alice")
    statements = []
    repository._db.set_trace_callback(statements.append)
    metadata = repository.metadata(SCOPE, "payroll", "v1")
    page = repository.list(SCOPE, "payroll")
    selects = [item.lower() for item in statements if item.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 2
    assert all("definition_json" not in query for query in selects)
    assert "private-reference" not in metadata.model_dump_json() + page.model_dump_json()


def test_both_quotas_allow_existing_retries_and_are_scoped(tmp_path):
    repo = SQLiteConnectionRepository(tmp_path / "limits.db", 1, 1)
    try:
        first = repo.put(SCOPE, "payroll", "v1", definition(), "alice")
        assert repo.put(SCOPE, "payroll", "v1", definition(), "bob") == first
        for connection_id, revision in (("payroll", "v2"), ("other", "v1")):
            with pytest.raises(ConnectionError) as error:
                repo.put(SCOPE, connection_id, revision, definition(), "alice")
            assert error.value.code == "limit_exceeded"
        other = SCOPE.model_copy(update={"environment_id": "uat"})
        assert repo.put(other, "payroll", "v1", definition(), "alice").revision == 1
        assert repo.list(SCOPE, "payroll").items == (first,)
    finally:
        repo.close()


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_constructor_rejects_invalid_quota(tmp_path, value):
    for options in ((value, 1), (1, value)):
        with pytest.raises(ValueError):
            SQLiteConnectionRepository(tmp_path / "bad.db", *options)


@pytest.mark.parametrize(
    "limit,before",
    [(0, None), (51, None), (True, None), (1.0, None), (1, 0), (1, True), (1, 2**63)],
)
def test_strict_pagination_bounds(repository, limit, before):
    with pytest.raises(ConnectionError) as error:
        repository.list(SCOPE, "payroll", limit, before)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("identifier", ["", "../foreign", "x" * 129, True])
def test_identifiers_fail_before_storage(repository, identifier):
    with pytest.raises(ConnectionError) as error:
        repository.put(SCOPE, identifier, "v1", definition(), "alice")
    assert error.value.code == "invalid_request"
    assert repository._db.execute("SELECT count(*) FROM http_connections").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["checksum", "metadata", "scope", "timestamp", "counter"])
def test_corrupt_metadata_rejected_by_metadata_get_and_list(repository, mutation):
    first = repository.put(SCOPE, "payroll", "v1", definition(), "alice")
    payload = first.model_dump(mode="json")
    checksum = digest(payload)
    if mutation == "checksum":
        checksum = "bad-private-reference"
    elif mutation == "metadata":
        payload["actor_id"] = "changed"
    elif mutation == "scope":
        payload["scope"]["organization_id"] = "foreign"
        checksum = digest(payload)
    elif mutation == "timestamp":
        payload["created_at"] = "2026-01-01T00:00:00"
        checksum = digest(payload)
    else:
        payload["revision"] = 2
        checksum = digest(payload)
    repository._db.execute(
        "UPDATE http_connections SET metadata_json=?,metadata_checksum=?",
        (canonical(payload), checksum),
    )
    repository._db.commit()
    for operation in (
        lambda: repository.metadata(SCOPE, "payroll", "v1"),
        lambda: repository.get(SCOPE, "payroll", "v1"),
        lambda: repository.list(SCOPE, "payroll"),
    ):
        with pytest.raises(ConnectionError) as error:
            operation()
        assert error.value.code == "unavailable"
        assert "private-reference" not in str(error.value)


@pytest.mark.parametrize(
    "raw",
    ['{"base_origin":"changed"}', "[]", '{"x":1,"x":2}', '"secret"', "x" * 16385],
    ids=["changed", "array", "duplicate", "scalar", "oversize"],
)
def test_private_corruption_does_not_force_public_reads_to_load_definitions(repository, raw):
    first = repository.put(SCOPE, "payroll", "v1", definition(), "alice")
    repository._db.execute("UPDATE http_connections SET definition_json=?", (raw,))
    repository._db.commit()
    assert repository.metadata(SCOPE, "payroll", "v1") == first
    assert repository.list(SCOPE, "payroll").items == (first,)
    with pytest.raises(ConnectionError) as error:
        repository.get(SCOPE, "payroll", "v1")
    assert error.value.code == "unavailable"
    with pytest.raises(ConnectionError):
        repository.put(SCOPE, "payroll", "v1", definition(), "alice")


def test_atomic_insert_failure_rolls_back_revision_and_can_retry(repository):
    first = repository.put(SCOPE, "payroll", "v1", definition(), "alice")
    repository._db.execute(
        "CREATE TRIGGER fail_connection BEFORE INSERT ON http_connections "
        "BEGIN SELECT RAISE(ABORT,'private-failure-sentinel'); END"
    )
    repository._db.commit()
    with pytest.raises(ConnectionError) as error:
        repository.put(SCOPE, "payroll", "v2", definition(), "alice")
    assert error.value.code == "unavailable" and "sentinel" not in str(error.value)
    assert repository.list(SCOPE, "payroll").items == (first,)
    repository._db.execute("DROP TRIGGER fail_connection")
    repository._db.commit()
    assert repository.put(SCOPE, "payroll", "v2", definition(), "alice").revision == 2


@pytest.mark.parametrize("changed", [False, True])
def test_concurrent_same_revision_has_one_immutable_winner(tmp_path, changed):
    path = tmp_path / "concurrent.db"
    repositories = [SQLiteConnectionRepository(path) for _ in range(2)]
    barrier = Barrier(2)

    def store(index):
        barrier.wait(timeout=10)
        try:
            return repositories[index].put(
                SCOPE,
                "payroll",
                "v1",
                definition(reference="other" if changed and index else "private-reference"),
                f"actor-{index}",
            )
        except ConnectionError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(store, (0, 1)))
        if changed:
            assert sum(result == "conflict" for result in results) == 1
        else:
            assert results[0] == results[1]
        assert len(repositories[0].list(SCOPE, "payroll").items) == 1
    finally:
        for repository in repositories:
            repository.close()


def test_concurrent_labels_receive_distinct_ordered_revision_numbers(tmp_path):
    path = tmp_path / "labels.db"
    repositories = [SQLiteConnectionRepository(path) for _ in range(2)]
    barrier = Barrier(2)

    def store(index):
        barrier.wait(timeout=10)
        return repositories[index].put(SCOPE, "payroll", f"v{index}", definition(), "alice")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(store, (0, 1)))
        assert sorted(item.revision for item in results) == [1, 2]
        assert [item.revision for item in repositories[0].list(SCOPE, "payroll").items] == [2, 1]
    finally:
        for repository in repositories:
            repository.close()


def test_closed_store_errors_are_safe_and_forged_models_revalidated(repository):
    bad = definition().model_copy(update={"base_origin": "https://user:raw-secret@example.com"})
    with pytest.raises(ConnectionError) as error:
        repository.put(SCOPE, "payroll", "v1", bad, "alice")
    assert error.value.code == "invalid_request" and "raw-secret" not in str(error.value)
    repository.close()
    with pytest.raises(ConnectionError) as error:
        repository.metadata(SCOPE, "payroll", "v1")
    assert error.value.code == "unavailable"
