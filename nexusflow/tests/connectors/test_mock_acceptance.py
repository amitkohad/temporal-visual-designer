"""Independent static-catalog/policy/executor acceptance without external effects.

The worker authorization adapter is synthetic. Explicit Dev scopes are trusted
host configuration, not live Entra or a persisted environment classification.
"""

import asyncio
import socket
from dataclasses import replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.execution import HTTPAttemptExecutor
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_scenarios import StaticHTTPMockScenarios, scenario_digest
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockGrant, HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest

PRIVATE = "never-log-mock-value"
DEV = Scope(organization_id="acme", environment_id="laboratory")


def scenario(scope=DEV):
    return HTTPMockScenario(
        scope=scope,
        connection_id="hr-api",
        connection_revision="fixture-v1",
        expected_path="/employees?private=" + PRIVATE,
        expected_request=HTTPAttemptRequest(method="POST", body=PRIVATE.encode()),
        status_code=201,
        headers=(("content-type", "application/json"), ("x-private", PRIVATE)),
        body=b'{"private":"' + PRIVATE.encode() + b'"}',
    )


def destination(value, operation_id="operation-1"):
    return HTTPDestinationRequest(
        connection_id=value.connection_id,
        connection_revision=value.connection_revision,
        operation_id=operation_id,
        path=value.expected_path,
    )


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert permission == "worker.execute"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "worker-1"


@pytest.fixture(autouse=True)
def no_live_effects(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Mocks must not access live providers"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    monkeypatch.setattr(HTTPAttemptExecutor, "execute", forbidden)
    yield forbidden
    forbidden.assert_not_called()


def components(value, *, scopes=None):
    worker = Worker()
    policy = ExplicitDevHTTPMockPolicy(
        worker, dev_scopes=(value.scope,) if scopes is None else scopes
    )
    catalog = StaticHTTPMockScenarios((value,))
    return worker, policy, catalog, HTTPMockExecutor(policy, catalog)


async def run(
    executor, value, *, operation_id="operation-1", pinned=None, selected=None, attempt=None
):
    return await executor.execute(
        value.scope,
        destination(value, operation_id) if selected is None else selected,
        value.expected_request if attempt is None else attempt,
        fixture_digest=scenario_digest(value) if pinned is None else pinned,
    )


def assert_private_failure(caught, code):
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert caught.value.delivery_may_have_occurred is False
    assert PRIVATE not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_actual_policy_catalog_executor_match_private_fixture_and_fresh_worker_checks():
    value = scenario()
    worker, _, _, executor = components(value)
    result = await run(executor, value)
    assert result.operation_id == "operation-1" and result.status_code == 201
    assert result.headers == value.headers and result.body == value.body
    assert len(worker.calls) == 2
    assert all(call == (DEV, "worker.execute", "hr-api") for call in worker.calls)
    assert PRIVATE not in repr(result) + repr(value) + repr(destination(value))


@pytest.mark.asyncio
async def test_repeated_reconstructed_and_concurrent_execution_is_deterministic():
    value = scenario()
    _, _, _, executor = components(value)
    first = await run(executor, value)
    assert await run(executor, value) == first
    _, _, _, reconstructed = components(scenario())
    assert await run(reconstructed, scenario()) == first
    results = await asyncio.gather(
        *(run(executor, value, operation_id=f"operation-{index}") for index in range(30))
    )
    assert [result.operation_id for result in results] == [f"operation-{i}" for i in range(30)]
    assert all(
        (r.status_code, r.headers, r.body) == (201, value.headers, value.body) for r in results
    )


@pytest.mark.asyncio
async def test_mutation_of_source_and_returned_fixture_cannot_poison_catalog():
    value = scenario()
    _, _, catalog, executor = components(value)
    pinned = scenario_digest(value)
    object.__setattr__(value, "body", b"changed-source")
    fetched = await catalog.get(DEV, "hr-api", "fixture-v1")
    assert fetched is not None
    object.__setattr__(fetched, "body", b"changed-read")
    object.__setattr__(fetched.expected_request, "body", b"changed-request")
    object.__setattr__(fetched.scope, "environment_id", "changed-scope")
    clean = scenario(Scope(organization_id="acme", environment_id="laboratory"))
    result = await run(executor, clean, pinned=pinned)
    assert result.body == clean.body


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["path", "method", "body", "content_type", "digest"])
async def test_fixture_mismatch_has_no_fallback_or_external_effect(mismatch):
    value = scenario()
    _, _, _, executor = components(value)
    selected, attempt, pinned = destination(value), value.expected_request, scenario_digest(value)
    if mismatch == "path":
        selected = selected.model_copy(update={"path": "/different"})
    elif mismatch == "method":
        attempt = replace(attempt, method="PATCH")
    elif mismatch == "body":
        attempt = replace(attempt, body=b"different")
    elif mismatch == "content_type":
        attempt = replace(attempt, content_type="text/plain")
    else:
        pinned = "sha256:" + "0" * 64
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value, selected=selected, attempt=attempt, pinned=pinned)
    assert_private_failure(caught, "fixture_mismatch")


@pytest.mark.asyncio
async def test_replaced_revision_contents_require_new_explicit_digest():
    original = scenario()
    changed = replace(original, body=b"changed-content")
    assert scenario_digest(original) != scenario_digest(changed)
    _, _, _, executor = components(changed)
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, original, pinned=scenario_digest(original))
    assert_private_failure(caught, "fixture_mismatch")
    assert (await run(executor, changed)).body == b"changed-content"


@pytest.mark.asyncio
async def test_missing_fixture_does_not_fall_back_to_live_executor():
    value = scenario()
    _, _, _, executor = components(value)
    selected = destination(value).model_copy(update={"connection_revision": "missing"})
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value, selected=selected)
    assert_private_failure(caught, "not_found")


@pytest.mark.asyncio
async def test_environment_name_does_not_grant_mock_eligibility():
    value = scenario(Scope(organization_id="acme", environment_id="dev"))
    worker, _, catalog, executor = components(value, scopes=())
    catalog.get = Mock(side_effect=AssertionError("Unauthorized fixture read"))
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value)
    assert_private_failure(caught, "forbidden")
    catalog.get.assert_not_called()
    assert worker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["uat", "prod", "unknown", True])
async def test_non_dev_or_malformed_kind_is_denied_before_fixture_access(kind):
    value = scenario()

    class Policy:
        def authorize(self, scope, permission, connection_id):
            assert permission == "connector.mock.execute"
            return HTTPMockGrant(scope, "worker-1", kind)

    catalog = StaticHTTPMockScenarios((value,))
    catalog.get = Mock(side_effect=AssertionError("Non-Dev fixture read"))
    executor = HTTPMockExecutor(Policy(), catalog)
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value)
    expected = "forbidden" if kind in {"uat", "prod"} else "invalid_provider_result"
    assert_private_failure(caught, expected)
    catalog.get.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_during_fixture_lookup_blocks_result_without_external_effect():
    value = scenario()
    worker, policy, catalog, _ = components(value)

    class RevokingCatalog:
        async def get(self, scope, connection_id, connection_revision):
            result = await catalog.get(scope, connection_id, connection_revision)
            worker.revoked = True
            return result

    executor = HTTPMockExecutor(policy, RevokingCatalog())
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value)
    assert_private_failure(caught, "forbidden")
    assert len(worker.calls) == 2


@pytest.mark.asyncio
async def test_scope_isolation_is_independent_of_identical_connection_identifiers():
    left = scenario()
    right = replace(
        scenario(Scope(organization_id="other-org", environment_id="laboratory")),
        body=b"other-organization",
    )
    worker = Worker()
    policy = ExplicitDevHTTPMockPolicy(worker, dev_scopes=(left.scope, right.scope))
    catalog = StaticHTTPMockScenarios((left, right))
    executor = HTTPMockExecutor(policy, catalog)
    assert (await run(executor, left)).body == left.body
    assert (await run(executor, right)).body == right.body
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, right, pinned=scenario_digest(left))
    assert_private_failure(caught, "fixture_mismatch")


@pytest.mark.asyncio
async def test_stalled_fixture_deadline_has_no_live_fallback(monkeypatch):
    value = scenario()
    worker, policy, _, _ = components(value)
    entered, stopped = asyncio.Event(), asyncio.Event()
    deadlines = []
    original_timeout = asyncio.timeout

    def capture_timeout(delay):
        deadline = original_timeout(delay)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    class StalledCatalog:
        async def get(self, scope, connection_id, connection_revision):
            try:
                entered.set()
                # Arm the real deadline after lookup starts, so slow setup cannot
                # turn this cancellation scenario into a pre-lookup timeout.
                deadlines[-1].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            finally:
                stopped.set()

    executor = HTTPMockExecutor(policy, StalledCatalog(), timeout_seconds=30)
    with pytest.raises(HTTPMockError) as caught:
        await run(executor, value)
    assert_private_failure(caught, "timeout")
    assert entered.is_set() and stopped.is_set() and len(worker.calls) == 1
    assert len(deadlines) == 1 and deadlines[0].expired()


@pytest.mark.asyncio
async def test_fixture_lookup_cancellation_propagates_without_a_result_or_external_effect():
    value = scenario()
    worker, policy, _, _ = components(value)
    entered, stopped = asyncio.Event(), asyncio.Event()

    class StalledCatalog:
        async def get(self, scope, connection_id, connection_revision):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    executor = HTTPMockExecutor(policy, StalledCatalog())
    task = asyncio.create_task(run(executor, value))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and len(worker.calls) == 1
