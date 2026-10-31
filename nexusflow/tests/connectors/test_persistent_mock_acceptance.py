"""Real SQLite/mock composition; no live provider, identity or default host claim."""

import asyncio
import socket
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_types import HTTPActivityError
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.project_versioning.canonical import canonical

DEV = Scope(organization_id="acme", environment_id="development")
PRIVATE = "private-stored-fixture-sentinel"
PAYLOAD = {"employee": PRIVATE, "nested": ["é", True, None]}


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert permission == "worker.execute" and connection_id == "hr"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "worker-1"


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Persistent mocks must not access network"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    yield
    forbidden.assert_not_called()


def scenario(scope=DEV):
    return HTTPMockScenario(
        scope=scope,
        connection_id="hr",
        connection_revision="fixture-v1",
        expected_path="/employees",
        expected_request=HTTPAttemptRequest(method="POST", body=canonical(PAYLOAD).encode()),
        status_code=201,
        headers=(("x-private", PRIVATE),),
        body=PRIVATE.encode(),
    )


def inputs(fixture_digest):
    return {
        "connection_id": "hr",
        "connection_revision": "fixture-v1",
        "execution_mode": "mock",
        "fixture_digest": fixture_digest,
        "method": "POST",
        "path": "/employees",
        "json_body": PAYLOAD,
    }


@contextmanager
def components(tmp_path, *, dev_scopes=(DEV,)):
    path = tmp_path / "fixtures.db"
    original = scenario()
    first = SQLiteHTTPMockScenarios(path)
    try:
        receipt = first.put(original)
    finally:
        first.close()
    repository = SQLiteHTTPMockScenarios(path)
    worker = Worker()
    live = Mock()
    live.execute.side_effect = AssertionError("Mock execution must never select live provider")
    adapter = HTTPJSONActivityAdapter(
        live=live,
        mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(worker, dev_scopes), repository),
    )
    try:
        yield repository, worker, adapter, inputs(receipt.fixture_digest), receipt, original
    finally:
        live.execute.assert_not_called()
        repository.close()


async def assert_failure(adapter, value, code, *, scope=DEV):
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(scope, "operation-1", value)
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert caught.value.delivery_may_have_occurred is False
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_reopened_real_store_executes_mock_with_private_status_only_result(tmp_path):
    with components(tmp_path) as (_, worker, adapter, value, receipt, original):
        assert receipt.fixture_digest == scenario_digest(original)
        assert PRIVATE not in repr(receipt)
        result = await adapter.execute(DEV, "operation-1", value)
        assert result.model_dump() == {
            "operation_id": "operation-1",
            "status_code": 201,
            "execution_mode": "mock",
        }
        assert PRIVATE not in repr(result) + result.model_dump_json()
        assert len(worker.calls) == 2


@pytest.mark.asyncio
async def test_repeat_and_concurrent_reads_after_restart_remain_deterministic(tmp_path):
    with components(tmp_path) as (repository, _, adapter, value, receipt, original):
        results = await asyncio.gather(
            *(adapter.execute(DEV, f"operation-{index}", value) for index in range(12))
        )
        assert [result.operation_id for result in results] == [f"operation-{i}" for i in range(12)]
        assert {result.status_code for result in results} == {201}
        assert {result.execution_mode for result in results} == {"mock"}
        assert repository.put(original) == receipt


@pytest.mark.asyncio
async def test_original_and_returned_fixture_mutation_cannot_change_stored_execution(tmp_path):
    with components(tmp_path) as (repository, _, adapter, value, receipt, original):
        object.__setattr__(original.expected_request, "body", b"changed")
        stored = await repository.get(DEV, "hr", "fixture-v1")
        assert stored is not None
        object.__setattr__(stored, "status_code", 500)
        object.__setattr__(stored.scope, "environment_id", "changed")
        fresh = await repository.get(DEV, "hr", "fixture-v1")
        assert fresh is not None and fresh.status_code == 201
        assert scenario_digest(fresh) == receipt.fixture_digest
        assert (await adapter.execute(DEV, "operation-1", value)).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"fixture_digest": "sha256:" + "0" * 64},
        {"path": "/other"},
        {"method": "PUT"},
        {"json_body": {"employee": "other"}},
    ],
)
async def test_persistent_fixture_exact_match_is_enforced_without_fallback(tmp_path, updates):
    with components(tmp_path) as (_, _, adapter, value, _, _):
        await assert_failure(adapter, value | updates, "fixture_mismatch")


@pytest.mark.asyncio
async def test_missing_revision_is_private_not_found_after_reopen(tmp_path):
    with components(tmp_path) as (_, _, adapter, value, _, _):
        await assert_failure(adapter, value | {"connection_revision": "missing"}, "not_found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other",
    [
        Scope(organization_id="other", environment_id="development"),
        Scope(organization_id="acme", environment_id="other"),
    ],
)
async def test_authorized_other_scope_cannot_read_persistent_fixture(tmp_path, other):
    with components(tmp_path, dev_scopes=(DEV, other)) as (repository, _, adapter, value, _, _):
        assert await repository.get(other, "hr", "fixture-v1") is None
        await assert_failure(adapter, value, "not_found", scope=other)


@pytest.mark.asyncio
async def test_persisted_fixture_does_not_grant_dev_eligibility(tmp_path):
    with components(tmp_path, dev_scopes=()) as (_, worker, adapter, value, _, _):
        await assert_failure(adapter, value, "forbidden")
        assert worker.calls == []


@pytest.mark.asyncio
async def test_revoked_worker_cannot_use_previously_persisted_fixture(tmp_path):
    with components(tmp_path) as (_, worker, adapter, value, _, _):
        worker.revoked = True
        await assert_failure(adapter, value, "forbidden")
        assert len(worker.calls) == 1


@pytest.mark.asyncio
async def test_fresh_authorization_after_real_sqlite_lookup_denies_revocation(tmp_path):
    with components(tmp_path) as (repository, worker, _, value, _, _):

        class RevokeAfterLookup:
            async def get(self, scope, connection_id, connection_revision):
                result = await repository.get(scope, connection_id, connection_revision)
                worker.revoked = True
                return result

        adapter = HTTPJSONActivityAdapter(
            mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(worker, (DEV,)), RevokeAfterLookup())
        )
        await assert_failure(adapter, value, "forbidden")
        assert len(worker.calls) == 2


@pytest.mark.asyncio
async def test_invalid_live_mode_does_not_become_a_persistent_mock_fallback(tmp_path):
    with components(tmp_path) as (_, worker, adapter, value, _, _):
        await assert_failure(adapter, value | {"execution_mode": "live"}, "invalid_request")
        assert worker.calls == []


@pytest.mark.asyncio
async def test_independent_connection_can_recover_same_private_fixture(tmp_path):
    with components(tmp_path) as (repository, _, adapter, value, receipt, _):
        other = SQLiteHTTPMockScenarios(tmp_path / "fixtures.db")
        try:
            stored = await other.get(DEV, "hr", "fixture-v1")
            assert stored is not None and scenario_digest(stored) == receipt.fixture_digest
            assert other.put(replace(stored)) == receipt
            assert (await adapter.execute(DEV, "operation-1", value)).status_code == 201
        finally:
            other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["fixture_digest", "definition_json"])
async def test_corrupted_persistent_fixture_fails_closed_without_private_diagnostics(
    tmp_path, column
):
    with components(tmp_path) as (_, _, adapter, value, _, _):
        with sqlite3.connect(tmp_path / "fixtures.db") as database:
            database.execute(f"UPDATE http_mock_scenarios SET {column}=?", (PRIVATE,))
        await assert_failure(adapter, value, "unavailable")
