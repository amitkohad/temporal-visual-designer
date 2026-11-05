"""Signed fixture administration to opt-in real Temporal worker acceptance."""

# ruff: noqa: F811

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from nexusflow.apps.api import create_app
from nexusflow.apps.persistent_http_mocks import open_persistent_http_mock_activities
from nexusflow.modules.connectors.execution import HTTPAttemptExecutor
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.resolver import AsyncIPv4Resolver
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.durable_runtime.types import DebugCommand
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.runtime.test_http_mock_dispatch_integration import (
    SCOPE,
    RetryAfterMockResponse,
    plan,
    start,
)
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
PRIVATE = "persistent-fixture-response-never-in-history"
PATH = BASE + "/connections/hr/revisions/fixture-v1/mock-fixture"
BODY = {
    "method": "POST",
    "path": "/employees",
    "json_body": {"employee": "example"},
    "status_code": 201,
    "response_json_body": {"private_response": PRIVATE},
}


class WorkerAuthorization:
    def __init__(self):
        self.revoked = False
        self.calls = []

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert scope == SCOPE and permission == "worker.execute" and connection_id == "hr"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "synthetic-persistent-worker"


@pytest.fixture(autouse=True)
def forbid_live_ports(monkeypatch):
    sentinels = []
    for owner, method in (
        (HTTPAttemptExecutor, "execute"),
        (AsyncIPv4Resolver, "resolve_ipv4"),
        (PinnedHTTPTransport, "send"),
    ):
        sentinel = AsyncMock(side_effect=AssertionError("Persistent mocks must not use live ports"))
        monkeypatch.setattr(owner, method, sentinel)
        sentinels.append(sentinel)
    yield
    for sentinel in sentinels:
        sentinel.assert_not_called()


@pytest.fixture
def saved_fixture(api, tmp_path):
    original, backend, data, runtime = api
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "persistent-fixtures.db"
    store = SQLiteHTTPMockScenarios(database)
    configured = replace(backend, mock_fixtures=store, mock_fixture_dev_scopes=(scope,))
    try:
        with TestClient(create_app(configured), raise_server_exceptions=False) as client:
            client.headers.update(original.headers)
            response = client.put(PATH, json=BODY)
            assert response.status_code == 201, response.text
            metadata = response.json()
            assert PRIVATE not in response.text
            assert client.get(PATH).json() == metadata
            assert backend.projects.list_projects(scope) == () and runtime.plans == {}
            yield client, store, database, metadata
    finally:
        store.close()


def inputs(metadata, *, body=BODY):
    return {
        "connection_id": metadata["connection_id"],
        "connection_revision": metadata["connection_revision"],
        "execution_mode": "mock",
        "fixture_digest": metadata["fixture_digest"],
        "method": body["method"],
        "path": body["path"],
        "json_body": body["json_body"],
    }


def open_activities(database, authorization, client, queue):
    return open_persistent_http_mock_activities(
        database,
        authorization,
        SCOPE,
        client.namespace,
        queue,
        dev_scopes=(SCOPE,),
    )


async def assert_private_history(handle):
    history = await handle.fetch_history()
    assert PRIVATE not in history.to_json()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)
    return history


async def test_signed_fixture_survives_worker_store_restart_and_replay(temporal_env, saved_fixture):
    _, admin_store, database, metadata = saved_fixture
    admin_store.close()
    before = database.read_bytes()
    authorization = WorkerAuthorization()
    client, queue = temporal_env.client, "persistent-debug-" + uuid4().hex
    with open_activities(database, authorization, client, queue) as activities:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[activities.dispatch]
        ):
            handle = await start(client, queue, plan(inputs(metadata), debug=True))
            snapshot = await wait_state(handle, "paused", "http")
            assert snapshot.completed_nodes == ("start",) and authorization.calls == []
    # The new host/provider is composed from the same persisted store, not a
    # copied static catalog; neither factory grants worker permission.
    with open_activities(database, authorization, client, queue) as activities:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[activities.dispatch]
        ):
            resumed = await wait_state(handle, "paused", "http")
            assert resumed.revision == 0
            await handle.execute_update(
                ProofWorkflow.control, DebugCommand("persistent-continue", "http", "continue", 0)
            )
            result = await asyncio.wait_for(handle.result(), timeout=30)
            assert result.completed_nodes == ("start", "http", "end")
            output = json.loads(result.activity_outputs["http"])
            assert set(output) == {"operation_id", "status_code", "execution_mode"}
            assert output["status_code"] == 201 and output["execution_mode"] == "mock"
            assert output["operation_id"].startswith("http-")
            assert len(authorization.calls) == 2
    assert database.read_bytes() == before
    await assert_private_history(handle)


async def test_persistent_retry_identity_and_later_admin_revision_are_observed(
    temporal_env, saved_fixture
):
    api_client, admin_store, database, metadata = saved_fixture
    authorization = WorkerAuthorization()
    client, queue = temporal_env.client, "persistent-retry-" + uuid4().hex
    with open_activities(database, authorization, client, queue) as activities:
        retry = RetryAfterMockResponse(activities)
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[retry.run]
        ):
            execution = "persistent-repeat-" + uuid4().hex
            first = await start(
                client, queue, plan(inputs(metadata), attempts=2), execution=execution
            )
            await asyncio.wait_for(first.result(), timeout=30)
            assert retry.attempts == [1, 2] and retry.operation_ids[0] == retry.operation_ids[1]
            second = await start(
                client, queue, plan(inputs(metadata), attempts=2), execution=execution
            )
            await asyncio.wait_for(second.result(), timeout=30)
            assert retry.attempts == [1, 2, 1, 2]
            assert retry.operation_ids[2] == retry.operation_ids[3]
            assert retry.operation_ids[0] != retry.operation_ids[2]
            # The admin writes independently while the worker holds its read-only
            # connection. Later activity reads must see the new committed revision.
            changed = BODY | {"status_code": 202, "json_body": {"employee": "new-example"}}
            path = PATH.replace("/revisions/fixture-v1/", "/revisions/fixture-v2/")
            response = api_client.put(path, json=changed)
            assert response.status_code == 201, response.text
            latest = response.json()
            third = await start(client, queue, plan(inputs(latest, body=changed), attempts=2))
            result = await asyncio.wait_for(third.result(), timeout=30)
            assert json.loads(result.activity_outputs["http"])["status_code"] == 202
            assert latest["fixture_digest"] != metadata["fixture_digest"]
            assert api_client.get(PATH).json() == metadata
            assert len(admin_store.list_metadata(SCOPE).items) == 2
    for handle in (first, second, third):
        await assert_private_history(handle)


@pytest.mark.parametrize(
    ("denial", "code", "reads"),
    [
        ("live", "forbidden", 0),
        ("revoked", "forbidden", 0),
        ("revoked-after-read", "forbidden", 1),
        ("missing", "not_found", 1),
        ("pin", "fixture_mismatch", 1),
    ],
)
async def test_persistent_worker_denials_are_private_without_live_fallback(
    temporal_env, saved_fixture, monkeypatch, denial, code, reads
):
    _, _, database, metadata = saved_fixture
    authorization = WorkerAuthorization()
    invoked = []
    original = SQLiteHTTPMockScenarios.get

    async def observed(self, *args):
        invoked.append(args)
        result = await original(self, *args)
        if denial == "revoked-after-read":
            authorization.revoked = True
        return result

    monkeypatch.setattr(SQLiteHTTPMockScenarios, "get", observed)
    selected = inputs(metadata)
    if denial == "live":
        selected |= {"execution_mode": "live", "fixture_digest": None}
    elif denial == "revoked":
        authorization.revoked = True
    elif denial == "missing":
        selected["connection_revision"] = "missing"
    elif denial == "pin":
        selected["fixture_digest"] = "sha256:" + "f" * 64
    client, queue = temporal_env.client, "persistent-denial-" + uuid4().hex
    with open_activities(database, authorization, client, queue) as activities:
        async with Worker(
            client, task_queue=queue, workflows=[ProofWorkflow], activities=[activities.dispatch]
        ):
            handle = await start(client, queue, plan(selected, attempts=3))
            with pytest.raises(WorkflowFailureError) as caught:
                await asyncio.wait_for(handle.result(), timeout=30)
            cause = caught.value.cause.cause
            assert isinstance(cause, ApplicationError) and cause.non_retryable
            assert cause.type == "HTTPMockDispatch." + code
            assert PRIVATE not in str(caught.value) + str(cause)
    assert len(invoked) == reads
    history = await assert_private_history(handle)
    failures = [
        event for event in history.events if event.HasField("activity_task_failed_event_attributes")
    ]
    assert len(failures) == 1
