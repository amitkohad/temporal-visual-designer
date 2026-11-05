"""Explicit persistent worker composition uses real storage and private mock ports."""

import asyncio
import json
import socket
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from temporalio.exceptions import ApplicationError

from nexusflow.apps import persistent_http_mocks as composition
from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors import temporal_mocks
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.activity_types import HTTPActivityError
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding
from nexusflow.modules.project_versioning.canonical import canonical

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-return-private-fixture"


class Authorization:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, resource_id):
        self.calls.append((scope, permission, resource_id))
        if self.revoked:
            raise PermissionError(PRIVATE)
        assert scope == SCOPE and permission == "worker.execute" and resource_id == "hr"
        return "worker-1"


def scenario():
    return HTTPMockScenario(
        scope=SCOPE,
        connection_id="hr",
        connection_revision="v1",
        expected_path="/employees",
        expected_request=HTTPAttemptRequest(
            method="POST", body=canonical({"employee": "sample"}).encode()
        ),
        status_code=201,
        headers=(("x-private", PRIVATE),),
        body=PRIVATE.encode(),
    )


def inputs(value=None):
    value = value or scenario()
    return {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "mock",
        "fixture_digest": scenario_digest(value),
        "method": "POST",
        "path": "/employees",
        "json_body": {"employee": "sample"},
    }


def opened(database, worker=None, **changes):
    arguments = {
        "database": database,
        "worker_authorization": worker or Authorization(),
        "scope": SCOPE,
        "namespace": "nexus",
        "task_queue": "worker",
        "dev_scopes": (SCOPE,),
    }
    arguments.update(changes)
    return composition.open_persistent_http_mock_activities(**arguments)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "fixtures.db"
    store = SQLiteHTTPMockScenarios(path)
    try:
        store.put(scenario())
    finally:
        store.close()
    return path


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No live mock worker fallback"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    yield forbidden
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": None},
        {"scope": Scope.model_construct(organization_id=1, environment_id="dev")},
        {"namespace": ""},
        {"namespace": "wrong/private"},
        {"namespace": "x" * 256},
        {"namespace": 1},
        {"task_queue": ""},
        {"task_queue": " leading"},
        {"task_queue": "trailing "},
        {"task_queue": "line\nbreak"},
        {"task_queue": "é"},
        {"task_queue": "x" * 256},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": True},
        {"timeout_seconds": 31},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "1"},
        {"dev_scopes": []},
        {"dev_scopes": ()},
        {"dev_scopes": (SCOPE, SCOPE)},
        {"dev_scopes": (Scope(organization_id="other", environment_id="dev"),)},
        {"dev_scopes": (None,)},
        {"worker_authorization": None},
    ],
)
def test_invalid_configuration_never_opens_provider(tmp_path, monkeypatch, changes):
    provider = Mock(side_effect=AssertionError("Provider must not open"))
    monkeypatch.setattr(composition, "SQLiteHTTPMockScenarios", provider)
    with pytest.raises(ValueError) as error, opened(tmp_path / PRIVATE, **changes):
        pytest.fail("Invalid configuration accepted")
    assert str(error.value) == "Invalid persistent HTTP mock worker configuration"
    assert PRIVATE not in str(error.value) + repr(error.value)
    assert error.value.__suppress_context__
    provider.assert_not_called()


def test_missing_database_remains_missing_and_private(tmp_path):
    missing = tmp_path / PRIVATE
    with pytest.raises(HTTPMockStorageError) as error, opened(missing):
        pytest.fail("Missing database accepted")
    assert error.value.code == "unavailable"
    assert PRIVATE not in str(error.value) + repr(error.value)
    assert not missing.exists()


def test_readonly_store_owned_without_eager_authorization(database):
    worker = Authorization()
    with opened(database, worker, timeout_seconds=2) as activities:
        assert worker.calls == []
        assert activities.adapter.live is None
        executor = activities.adapter.mock
        assert executor.timeout_seconds == 2
        assert activities.timeout_seconds == 2
        assert activities.adapter.timeout_seconds == 2
        store = executor.scenarios
        with pytest.raises(HTTPMockStorageError) as error:
            store.put(scenario())
        assert error.value.code == "unavailable"
        assert asyncio.run(store.get(SCOPE, "hr", "v1")) == scenario()
    with pytest.raises(HTTPMockError) as error:
        asyncio.run(store.get(SCOPE, "hr", "v1"))
    assert error.value.code == "unavailable"


def test_outer_deadline_does_not_expand_default_mock_budget(database):
    with opened(database) as activities:
        assert activities.adapter.mock.timeout_seconds == 5
        assert activities.timeout_seconds == 30


@pytest.mark.asyncio
async def test_actual_persisted_response_returns_only_metadata(database):
    worker = Authorization()
    for _ in range(2):
        with opened(database, worker) as activities:
            output = await activities.adapter.execute(SCOPE, "operation-1", inputs())
            assert output.model_dump() == {
                "operation_id": "operation-1",
                "status_code": 201,
                "execution_mode": "mock",
            }
            assert PRIVATE not in output.model_dump_json()
    assert len(worker.calls) == 4


@pytest.mark.asyncio
async def test_fresh_revocation_prevents_lookup_and_result(database, monkeypatch):
    worker = Authorization()
    with opened(database, worker) as activities:
        store = activities.adapter.mock.scenarios
        original = store.get
        calls = []

        async def revoke_after_read(*args):
            calls.append(args)
            result = await original(*args)
            worker.revoked = True
            return result

        monkeypatch.setattr(store, "get", revoke_after_read)
        with pytest.raises(HTTPActivityError) as error:
            await activities.adapter.execute(SCOPE, "operation-1", inputs())
        assert error.value.code == "forbidden" and PRIVATE not in str(error.value)
        assert len(calls) == 1 and len(worker.calls) == 2
        with pytest.raises(HTTPActivityError) as error:
            await activities.adapter.execute(SCOPE, "operation-2", inputs())
        assert error.value.code == "forbidden" and len(calls) == 1


@pytest.mark.asyncio
async def test_live_mode_has_no_executor_or_fallback(database):
    with opened(database) as activities:
        with pytest.raises(HTTPActivityError) as error:
            await activities.adapter.execute(
                SCOPE, "operation-1", {**inputs(), "execution_mode": "live", "fixture_digest": None}
            )
        assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_delivered_cancellation_propagates(database, monkeypatch):
    with opened(database) as activities:
        store = activities.adapter.mock.scenarios
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def stalled(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(store, "get", stalled)
        task = asyncio.create_task(activities.adapter.execute(SCOPE, "operation-1", inputs()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()


def test_context_body_exception_propagates_and_closes(database):
    failure = RuntimeError(PRIVATE)
    with pytest.raises(RuntimeError) as error:
        with opened(database) as activities:
            store = activities.adapter.mock.scenarios
            raise failure
    assert error.value is failure
    with pytest.raises(HTTPMockError) as error:
        asyncio.run(store.get(SCOPE, "hr", "v1"))
    assert error.value.code == "unavailable"


def test_construction_failure_closes_store_and_sanitizes(database, monkeypatch):
    original = composition.SQLiteHTTPMockScenarios
    stores = []

    def record(*args, **kwargs):
        store = original(*args, **kwargs)
        stores.append(store)
        assert kwargs == {"read_only": True}
        return store

    monkeypatch.setattr(composition, "SQLiteHTTPMockScenarios", record)
    monkeypatch.setattr(
        composition, "HTTPJSONActivityAdapter", Mock(side_effect=RuntimeError(PRIVATE))
    )
    with pytest.raises(ValueError) as error, opened(database):
        pytest.fail("Construction failure ignored")
    assert str(error.value) == "Invalid persistent HTTP mock worker configuration"
    assert PRIVATE not in str(error.value)
    assert len(stores) == 1
    with pytest.raises(HTTPMockError) as error:
        asyncio.run(stores[0].get(SCOPE, "hr", "v1"))
    assert error.value.code == "unavailable"


def test_private_storage_failure_is_preserved_without_reclassification(tmp_path, monkeypatch):
    failure = HTTPMockStorageError("unavailable")
    monkeypatch.setattr(composition, "SQLiteHTTPMockScenarios", Mock(side_effect=failure))
    with pytest.raises(HTTPMockStorageError) as error, opened(tmp_path / "existing.db"):
        pytest.fail("Storage failure ignored")
    assert error.value is failure


def test_scope_declarations_are_copied_and_not_inferred_from_label(database):
    selected = Scope(organization_id="acme", environment_id="laboratory")
    with opened(database, scope=selected, dev_scopes=(selected,)) as activities:
        object.__setattr__(selected, "environment_id", "changed")
        assert activities.scope.environment_id == "laboratory"
        policy = activities.adapter.mock.policy
        worker = policy._worker
        worker.authorize = lambda scope, permission, resource: "worker-1"
        grant = policy.authorize(activities.scope, "connector.mock.execute", "hr")
        assert grant.scope.environment_id == "laboratory" and grant.environment_kind == "dev"


@pytest.mark.asyncio
async def test_composed_bridge_validates_actual_context_and_keeps_private_body(
    database, monkeypatch
):
    info = SimpleNamespace(
        namespace="nexus",
        workflow_namespace="nexus",
        task_queue="worker",
        workflow_type="NexusFlowProofV1",
        workflow_id="execution-1",
        workflow_run_id="run-1",
        activity_type="nexusflow.proof.dispatch",
        activity_id="http-step",
        attempt=1,
    )
    monkeypatch.setattr(temporal_mocks.activity, "info", lambda: info)
    reference = http_json_activity_descriptor().activity_ref
    call = ActivityCall(
        binding=ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        node_id="http-step",
        operation_id="execution-1:http-step",
        activity_id=reference.activity_id,
        activity_version=reference.version,
        artifact_digest=reference.artifact_digest,
        input_json=canonical(inputs()),
        workflow_input_json="{}",
    )
    with opened(database) as activities:
        result = json.loads(await activities.dispatch(call))
        assert result["operation_id"].startswith("http-") and result["status_code"] == 201
        assert result["execution_mode"] == "mock" and PRIVATE not in canonical(result)
        with pytest.raises(ApplicationError) as error:
            await activities.dispatch(
                replace(
                    call,
                    input_json=canonical(
                        {**inputs(), "execution_mode": "live", "fixture_digest": None}
                    ),
                )
            )
        assert error.value.type == "HTTPMockDispatch.forbidden"
        assert error.value.non_retryable and PRIVATE not in str(error.value)
