"""Mock executor tests use private fixture and policy fakes without live adapters."""

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockGrant, HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest

SCOPE = Scope(organization_id="acme", environment_id="not-a-dev-name")
DESTINATION = HTTPDestinationRequest(
    connection_id="hr-api", connection_revision="v1", operation_id="run-1-step-1", path="/employee"
)
ATTEMPT = HTTPAttemptRequest(method="POST", body=b'{"employee":"private-fixture-value"}')
SCENARIO = HTTPMockScenario(
    SCOPE,
    "hr-api",
    "v1",
    "/employee",
    ATTEMPT,
    201,
    (("content-type", "application/json"),),
    b'{"response":"private-fixture-value"}',
)
PRIVATE = "private-fixture-value"


class Policy:
    def __init__(self, events):
        self.events = events
        self.grant = HTTPMockGrant(SCOPE, "worker-1", "dev")
        self.error = None

    def authorize(self, scope, permission, connection_id):
        self.events.append(("authorize", scope, permission, connection_id))
        if self.error:
            raise self.error
        return self.grant


@pytest.fixture
def ports():
    events = []
    policy = Policy(events)
    scenarios = type("Scenarios", (), {})()

    async def get(scope, connection_id, revision):
        events.append(("get", scope, connection_id, revision))
        return SCENARIO

    scenarios.get = AsyncMock(side_effect=get)
    return policy, scenarios, events


async def call(
    ports,
    *,
    executor=None,
    scope=SCOPE,
    destination=DESTINATION,
    request=ATTEMPT,
    fixture_digest=None,
):
    executor = executor or HTTPMockExecutor(ports[0], ports[1])
    return await executor.execute(
        scope,
        destination,
        request,
        fixture_digest=scenario_digest(SCENARIO) if fixture_digest is None else fixture_digest,
    )


async def failure(ports, code, **kwargs):
    with pytest.raises(HTTPMockError) as caught:
        await call(ports, **kwargs)
    assert caught.value.code == code
    assert caught.value.retryable is False and caught.value.delivery_may_have_occurred is False
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_explicit_dev_grant_and_fixture_have_exact_order_and_private_result(ports):
    result = await call(ports)
    assert result.operation_id == DESTINATION.operation_id and result.status_code == 201
    assert result.body == SCENARIO.body and result.headers == SCENARIO.headers
    assert PRIVATE not in repr(result)
    assert [event[0] for event in ports[2]] == ["authorize", "get", "authorize"]
    assert ports[2][0][2:] == ("connector.mock.execute", "hr-api")


@pytest.mark.asyncio
async def test_repeated_concurrent_calls_are_deterministic_and_keep_each_operation(ports):
    results = await asyncio.gather(
        *[
            call(
                ports, destination=DESTINATION.model_copy(update={"operation_id": f"operation-{i}"})
            )
            for i in range(10)
        ]
    )
    assert [result.operation_id for result in results] == [f"operation-{i}" for i in range(10)]
    assert {result.body for result in results} == {SCENARIO.body}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["uat", "prod"])
async def test_non_dev_grant_denies_before_lookup_regardless_of_scope_name(ports, kind):
    ports[0].grant = HTTPMockGrant(SCOPE, "worker-1", kind)
    await failure(ports, "forbidden")
    assert ports[1].get.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grant",
    [
        None,
        False,
        "dev",
        HTTPMockGrant(SCOPE, "", "dev"),
        HTTPMockGrant(SCOPE, "worker-1", "DEV"),
        HTTPMockGrant(SCOPE, "worker-1", True),
        HTTPMockGrant(Scope(organization_id="other", environment_id="dev"), "worker-1", "dev"),
    ],
)
async def test_malformed_or_cross_scope_policy_result_is_not_authority(ports, grant):
    ports[0].grant = grant
    await failure(ports, "invalid_provider_result")
    assert ports[1].get.await_count == 0


@pytest.mark.asyncio
async def test_async_policy_is_rejected_and_closed_without_runtime_warning(ports, recwarn):
    async def invalid(*args):
        return HTTPMockGrant(SCOPE, "worker-1", "dev")

    ports[0].authorize = invalid
    await failure(ports, "invalid_provider_result")
    assert not recwarn.list and ports[1].get.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (PermissionError(PRIVATE), "forbidden"),
        (RuntimeError(PRIVATE), "unavailable"),
        (HTTPMockError("forbidden"), "forbidden"),
    ],
)
async def test_policy_errors_are_closed_and_private(ports, error, code):
    ports[0].error = error
    await failure(ports, code)
    assert ports[1].get.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "prod"])
async def test_policy_is_fresh_after_fixture_lookup(ports, change):
    async def get(*args):
        if change == "revoke":
            ports[0].error = PermissionError(PRIVATE)
        else:
            ports[0].grant = HTTPMockGrant(SCOPE, "worker-1", "prod")
        return SCENARIO

    ports[1].get.side_effect = get
    await failure(ports, "forbidden")
    assert ports[1].get.await_count == 1


@pytest.mark.asyncio
async def test_missing_fixture_still_rechecks_policy_and_has_no_live_fallback(ports):
    ports[1].get.side_effect = None
    ports[1].get.return_value = None
    await failure(ports, "not_found")
    assert len(ports[2]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        False,
        {},
        replace(SCENARIO, connection_id="other"),
        replace(SCENARIO, connection_revision="v2"),
        replace(SCENARIO, scope=Scope(organization_id="other", environment_id="dev")),
        replace(SCENARIO, status_code=True),
        replace(SCENARIO, headers=(("x", "bad\r\nvalue"),)),
    ],
)
async def test_bad_fixture_provider_results_fail_closed(ports, value):
    ports[1].get.side_effect = None
    ports[1].get.return_value = value
    await failure(ports, "invalid_provider_result")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"fixture_digest": "sha256:" + "0" * 64},
        {"destination": DESTINATION.model_copy(update={"path": "/different"})},
        {"request": HTTPAttemptRequest(method="PUT", body=ATTEMPT.body)},
        {"request": HTTPAttemptRequest(method="POST", body=b"other")},
        {
            "request": HTTPAttemptRequest(
                method="POST", body=ATTEMPT.body, content_type="text/plain"
            )
        },
    ],
)
async def test_fixture_selection_never_ignores_digest_or_request_mismatch(ports, kwargs):
    await failure(ports, "fixture_mismatch", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"fixture_digest": "private-bad-digest"},
        {"fixture_digest": False},
        {"scope": None},
        {"destination": None},
        {"request": HTTPAttemptRequest(method="CONNECT")},
        {"request": HTTPAttemptRequest(body=b"not-allowed-on-get")},
        {"destination": DESTINATION.model_copy(update={"path": "//other.example.com"})},
    ],
)
async def test_invalid_request_fails_before_policy_or_lookup(ports, kwargs):
    await failure(ports, "invalid_request", **kwargs)
    assert ports[2] == [] and ports[1].get.await_count == 0


@pytest.mark.asyncio
async def test_caller_input_snapshots_cannot_be_mutated_by_fixture_lookup(ports):
    scope = Scope(organization_id=SCOPE.organization_id, environment_id=SCOPE.environment_id)
    destination = DESTINATION.model_copy()
    request = HTTPAttemptRequest(method="POST", body=ATTEMPT.body)

    async def get(selected_scope, *args):
        object.__setattr__(scope, "organization_id", "changed")
        object.__setattr__(selected_scope, "organization_id", "provider-change")
        object.__setattr__(destination, "path", "/changed")
        object.__setattr__(request, "body", b"changed")
        return SCENARIO

    ports[1].get.side_effect = get
    result = await call(ports, scope=scope, destination=destination, request=request)
    assert result.body == SCENARIO.body


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max_request_bytes", "max_response_bytes", "max_header_bytes"])
async def test_configured_bounds_apply_without_exposing_fixture_data(ports, limit):
    if limit == "max_request_bytes":
        executor = HTTPMockExecutor(ports[0], ports[1], max_request_bytes=0)
        await failure(ports, "invalid_request", executor=executor)
    elif limit == "max_response_bytes":
        executor = HTTPMockExecutor(ports[0], ports[1], max_response_bytes=0)
        await failure(ports, "invalid_provider_result", executor=executor)
    else:
        ports[1].get.side_effect = None
        ports[1].get.return_value = replace(SCENARIO, headers=(("x", "a" * 2000),))
        executor = HTTPMockExecutor(ports[0], ports[1], max_header_bytes=1024)
        await failure(ports, "invalid_provider_result", executor=executor)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"max_request_bytes": -1},
        {"max_response_bytes": 4194305},
        {"max_header_bytes": 1023},
        {"max_header_bytes": True},
    ],
)
def test_invalid_configuration_is_rejected(ports, kwargs):
    with pytest.raises(ValueError):
        HTTPMockExecutor(ports[0], ports[1], **kwargs)


@pytest.mark.asyncio
async def test_fixture_provider_exceptions_cannot_expose_payload_or_fake_delivery(ports):
    error = HTTPMockError("unavailable")
    error.delivery_may_have_occurred = True
    error.retryable = True
    error.args = (PRIVATE,)
    ports[1].get.side_effect = error
    await failure(ports, "unavailable")


@pytest.mark.asyncio
async def test_timeout_cancels_stalled_fixture_provider(ports):
    stopped = asyncio.Event()

    async def stalled(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    ports[1].get.side_effect = stalled
    executor = HTTPMockExecutor(ports[0], ports[1], timeout_seconds=0.01)
    await failure(ports, "timeout", executor=executor)
    assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress", [False, True])
async def test_caller_cancellation_propagates_even_if_provider_suppresses_it(ports, suppress):
    entered = asyncio.Event()

    async def stalled(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if suppress:
                return SCENARIO
            raise

    ports[1].get.side_effect = stalled
    task = asyncio.create_task(call(ports))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_suppressed_timeout_cannot_return_success(ports):
    async def late(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return SCENARIO

    ports[1].get.side_effect = late
    await failure(
        ports, "timeout", executor=HTTPMockExecutor(ports[0], ports[1], timeout_seconds=0.01)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["authorize", "lookup"])
async def test_blocked_timeout_callback_cannot_allow_late_result(ports, phase):
    if phase == "authorize":
        original = ports[0].authorize

        def late(*args):
            time.sleep(0.02)
            return original(*args)

        ports[0].authorize = late
    else:

        async def late(*args):
            time.sleep(0.02)
            return SCENARIO

        ports[1].get.side_effect = late
    await failure(
        ports, "timeout", executor=HTTPMockExecutor(ports[0], ports[1], timeout_seconds=0.005)
    )
