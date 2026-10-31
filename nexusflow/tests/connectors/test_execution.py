"""Isolated attempt coordination; no DNS, TLS sockets, secrets or Temporal."""

import asyncio
import math
import time
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import (
    ApprovedHTTPDestination,
    DestinationError,
    HTTPDestinationRequest,
)
from nexusflow.modules.connectors.execution import HTTPAttemptExecutor
from nexusflow.modules.connectors.execution_types import HTTPExecutionError
from nexusflow.modules.connectors.transport_types import (
    HTTPAttemptRequest,
    HTTPAttemptResult,
    HTTPTransportError,
)

PRIVATE = "never-export-provider-value"
SCOPE = Scope(organization_id="acme", environment_id="dev")
DESTINATION = HTTPDestinationRequest(
    connection_id="hr-api", connection_revision="v1", operation_id="run-step-1", path="/" + PRIVATE
)
PLAN = ApprovedHTTPDestination(
    scope=SCOPE,
    connection_id=DESTINATION.connection_id,
    connection_revision=DESTINATION.connection_revision,
    definition_digest="sha256:" + "a" * 64,
    operation_id=DESTINATION.operation_id,
    url="https://api.example.com/" + PRIVATE,
    origin="https://api.example.com",
    host="api.example.com",
    port=443,
    ipv4_addresses=("1.1.1.1", "8.8.8.8"),
)
RESULT = HTTPAttemptResult(
    DESTINATION.operation_id, 200, (("x-private", PRIVATE),), PRIVATE.encode()
)


@pytest.fixture
def ports():
    events = []
    planner = Mock()

    async def prepare(scope, request):
        events.append("prepare")
        return PLAN

    def revalidate(scope, request, plan):
        events.append("authorize")

    planner.prepare_credential_free = AsyncMock(side_effect=prepare)
    planner.revalidate_credential_free = Mock(side_effect=revalidate)
    sender = Mock()

    async def send(plan, request, *, before_write):
        events.append("dial")
        before_write()
        events.append("write")
        return RESULT

    sender.send = AsyncMock(side_effect=send)
    return HTTPAttemptExecutor(planner, sender), planner, sender, events


async def failed(executor, *, code, possible=False, **kwargs):
    with pytest.raises(HTTPExecutionError) as caught:
        await executor.execute(
            kwargs.get("scope", SCOPE),
            kwargs.get("destination", DESTINATION),
            kwargs.get("request", HTTPAttemptRequest()),
        )
    assert caught.value.code == code
    assert caught.value.delivery_may_have_occurred is possible
    assert caught.value.retryable is False
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__
    return caught.value


@pytest.mark.asyncio
async def test_fresh_authorization_order_single_attempt_private_copied_result(ports):
    executor, planner, sender, events = ports
    request = HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
    result = await executor.execute(SCOPE, DESTINATION, request)
    assert events == ["prepare", "authorize", "dial", "authorize", "write"]
    assert result == RESULT and result is not RESULT
    assert PRIVATE not in repr(result) + repr(request)
    sent_plan, sent_request = sender.send.call_args.args
    assert sent_plan == PLAN and sent_plan is not PLAN
    assert sent_request == request and sent_request is not request
    prepared_scope, prepared_request = planner.prepare_credential_free.call_args.args
    assert prepared_scope is not SCOPE and prepared_request is not DESTINATION
    assert sender.send.await_count == 1 and planner.revalidate_credential_free.call_count == 2


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 121},
        {"timeout_seconds": math.inf},
        {"timeout_seconds": math.nan},
        {"timeout_seconds": 10**1000},
        {"max_request_bytes": True},
        {"max_request_bytes": -1},
        {"max_request_bytes": 4194305},
        {"max_response_bytes": -1},
        {"max_response_bytes": 4194305},
        {"max_header_bytes": True},
        {"max_header_bytes": 1023},
        {"max_header_bytes": 65537},
    ],
)
def test_invalid_configuration(options):
    with pytest.raises(ValueError, match="Invalid HTTP execution limits"):
        HTTPAttemptExecutor(Mock(), Mock(), **options)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt",
    [
        None,
        {},
        HTTPAttemptRequest(method="TRACE"),
        HTTPAttemptRequest(method="get"),
        HTTPAttemptRequest(body=b"not-empty"),
        HTTPAttemptRequest(method="HEAD", body=b"not-empty"),
        HTTPAttemptRequest(method="POST", body=bytearray(b"mutable")),
        HTTPAttemptRequest(method="POST", content_type="application/json\r\nx: private"),
        HTTPAttemptRequest(method="POST", body=b"x" * 1048577),
    ],
)
async def test_invalid_request_fails_before_private_lookup_or_dns(ports, attempt):
    executor, planner, sender, _ = ports
    await failed(executor, request=attempt, code="invalid_request")
    planner.prepare_credential_free.assert_not_awaited()
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": {}},
        {"scope": Scope.model_construct(organization_id=True, environment_id="dev")},
        {"destination": {}},
        {"destination": DESTINATION.model_copy(update={"connection_id": "bad/private"})},
        {"destination": DESTINATION.model_copy(update={"path": True})},
    ],
)
async def test_scope_and_destination_are_revalidated_before_private_access(ports, kwargs):
    executor, planner, sender, _ = ports
    await failed(executor, code="invalid_request", **kwargs)
    planner.prepare_credential_free.assert_not_awaited()
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["forbidden", "credentials_required", "blocked_destination", "unavailable"]
)
async def test_preflight_failure_has_no_remote_effect(ports, code):
    executor, planner, sender, _ = ports
    planner.prepare_credential_free.side_effect = DestinationError(code)
    await failed(executor, code=code)
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["after_dns", "before_write"])
@pytest.mark.parametrize("code", ["forbidden", "credentials_required", "unavailable"])
async def test_revocation_or_changed_secret_blocks_after_dns_and_before_write(ports, stage, code):
    executor, planner, sender, events = ports
    planner.revalidate_credential_free.side_effect = (
        [DestinationError(code)] if stage == "after_dns" else [None, DestinationError(code)]
    )
    await failed(executor, code=code)
    assert "write" not in events
    assert sender.send.await_count == (stage == "before_write")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        None,
        {},
        replace(PLAN, scope=Scope(organization_id="other", environment_id="dev")),
        replace(PLAN, connection_id="other"),
        replace(PLAN, connection_id=True),
        replace(PLAN, connection_revision="v2"),
        replace(PLAN, operation_id="other"),
        replace(PLAN, definition_digest="bad"),
        replace(
            PLAN,
            scope=Scope.model_construct(organization_id=PRIVATE.encode(), environment_id="dev"),
        ),
        replace(PLAN, redirect_count=True),
        replace(PLAN, redirect_count=1),
        replace(PLAN, port=True),
        replace(PLAN, host="other.example.com"),
        replace(PLAN, url="https://api.example.com/other"),
        replace(PLAN, ipv4_addresses=("127.0.0.1",)),
        replace(PLAN, ipv4_addresses=("8.8.8.8", "1.1.1.1")),
    ],
)
async def test_malformed_plan_is_never_sent(ports, plan):
    executor, planner, sender, _ = ports
    planner.prepare_credential_free.side_effect = None
    planner.prepare_credential_free.return_value = plan
    await failed(executor, code="invalid_provider_result")
    sender.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["after_dns", "before_write"])
async def test_invalid_revalidation_return_is_fail_closed(ports, stage):
    executor, planner, _, events = ports
    planner.revalidate_credential_free.side_effect = (
        [False] if stage == "after_dns" else [None, False]
    )
    await failed(
        executor, code="invalid_provider_result" if stage == "after_dns" else "unavailable"
    )
    assert "write" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ignored", "repeated", "caught_denial"])
async def test_sender_cannot_return_success_without_one_successful_guard(ports, mode):
    executor, planner, sender, _ = ports

    async def malformed(plan, request, *, before_write):
        if mode != "ignored":
            try:
                before_write()
                if mode == "repeated":
                    before_write()
            except HTTPTransportError:
                pass
        return RESULT

    sender.send.side_effect = malformed
    if mode == "caught_denial":
        planner.revalidate_credential_free.side_effect = [None, DestinationError("forbidden")]
    await failed(executor, code="invalid_provider_result", possible=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        replace(RESULT, operation_id="other"),
        replace(RESULT, status_code=True),
        replace(RESULT, status_code=199),
        replace(RESULT, status_code=600),
        replace(RESULT, headers=[]),
        replace(RESULT, headers=(("Bad-Case", "private"),)),
        replace(RESULT, headers=(("x-bad\n", "private"),)),
        replace(RESULT, headers=(("x-header", "private\n"),)),
        replace(RESULT, headers=(("x-header", "private\x00"),)),
        replace(RESULT, headers=(("x-header", "\u0100"),)),
        replace(RESULT, headers=(("x-header", ""),) * 101),
        replace(RESULT, headers=(("x-header", "x" * 16384),)),
        replace(RESULT, body=bytearray(b"mutable")),
        replace(RESULT, body=b"x" * 1048577),
    ],
)
async def test_malformed_or_oversized_results_are_private_and_potentially_ambiguous(ports, result):
    executor, _, sender, _ = ports

    async def send(plan, request, *, before_write):
        before_write()
        return result

    sender.send.side_effect = send
    await failed(executor, code="invalid_provider_result", possible=True)


@pytest.mark.asyncio
async def test_head_response_body_is_rejected(ports):
    executor, _, _, _ = ports
    await failed(
        executor,
        request=HTTPAttemptRequest(method="HEAD"),
        code="invalid_provider_result",
        possible=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 304])
async def test_bodyless_status_cannot_return_a_body(ports, status):
    executor, _, sender, _ = ports

    async def send(plan, request, *, before_write):
        before_write()
        return replace(RESULT, status_code=status)

    sender.send.side_effect = send
    await failed(executor, code="invalid_provider_result", possible=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["after_dns", "before_write"])
async def test_async_revalidator_is_rejected_and_closed_without_warning(ports, stage, recwarn):
    executor, planner, _, events = ports
    count = 0

    async def async_validation():
        raise AssertionError("An async revalidator must never execute")

    def validate(*args):
        nonlocal count
        count += 1
        return None if stage == "before_write" and count == 1 else async_validation()

    planner.revalidate_credential_free.side_effect = validate
    await failed(
        executor, code="invalid_provider_result" if stage == "after_dns" else "unavailable"
    )
    assert "write" not in events and not list(recwarn)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 301, 302, 307, 308, 404, 500, 599])
async def test_statuses_and_redirects_return_privately_without_following_or_retry(ports, status):
    executor, _, sender, _ = ports

    async def send(plan, request, *, before_write):
        before_write()
        return HTTPAttemptResult(
            DESTINATION.operation_id,
            status,
            (("location", "https://other.invalid/" + PRIVATE),),
            b"",
        )

    sender.send.side_effect = send
    result = await executor.execute(SCOPE, DESTINATION, HTTPAttemptRequest())
    assert result.status_code == status and sender.send.await_count == 1
    assert PRIVATE not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("possible", [False, True])
async def test_known_transport_uncertainty_is_preserved(ports, possible):
    executor, _, sender, _ = ports
    sender.send.side_effect = HTTPTransportError(
        "protocol_error", delivery_may_have_occurred=possible
    )
    await failed(executor, code="protocol_error", possible=possible)


@pytest.mark.asyncio
async def test_unknown_post_entry_failure_is_potentially_ambiguous(ports):
    executor, _, sender, _ = ports
    sender.send.side_effect = RuntimeError(PRIVATE)
    await failed(executor, code="unavailable", possible=True)


@pytest.mark.asyncio
async def test_malformed_transport_error_never_downgrades_unknown_delivery(ports):
    executor, _, sender, _ = ports
    error = HTTPTransportError("unavailable")
    error.code = PRIVATE
    error.delivery_may_have_occurred = None
    sender.send.side_effect = error
    await failed(executor, code="unavailable", possible=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "send"])
async def test_total_timeout_bounds_entire_attempt(ports, phase):
    _, planner, sender, _ = ports

    async def stall(*args, **kwargs):
        await asyncio.Event().wait()

    target = planner.prepare_credential_free if phase == "prepare" else sender.send
    target.side_effect = stall
    await failed(
        HTTPAttemptExecutor(planner, sender, timeout_seconds=0.01),
        code="timeout",
        possible=phase == "send",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "send"])
async def test_caller_cancellation_even_when_provider_suppresses_it(ports, phase):
    executor, planner, sender, _ = ports
    entered = asyncio.Event()

    async def suppress(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return PLAN if phase == "prepare" else RESULT

    target = planner.prepare_credential_free if phase == "prepare" else sender.send
    target.side_effect = suppress
    task = asyncio.create_task(executor.execute(SCOPE, DESTINATION, HTTPAttemptRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if phase == "prepare":
        sender.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "send"])
async def test_suppressed_timeout_late_provider_result_is_rejected(ports, phase, monkeypatch):
    _, planner, sender, _ = ports
    original_timeout = asyncio.timeout
    deadlines = []
    suppressed = asyncio.Event()

    def capture_timeout(delay):
        deadline = original_timeout(delay)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr("nexusflow.modules.connectors.execution.asyncio.timeout", capture_timeout)

    async def suppress(*args, **kwargs):
        # Expire the real production deadline only after the selected provider
        # has entered. Setup duration must not change send-delivery uncertainty.
        assert len(deadlines) == 1 and not deadlines[0].expired()
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            suppressed.set()
            return PLAN if phase == "prepare" else RESULT

    target = planner.prepare_credential_free if phase == "prepare" else sender.send
    target.side_effect = suppress
    await failed(
        HTTPAttemptExecutor(planner, sender, timeout_seconds=30),
        code="timeout",
        possible=phase == "send",
    )
    assert suppressed.is_set() and deadlines[0].expired()
    target.assert_awaited_once()
    if phase == "prepare":
        sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_revalidation_that_delays_timeout_cannot_enter_sender(ports):
    _, planner, sender, _ = ports
    planner.revalidate_credential_free.side_effect = lambda *args: time.sleep(0.02)
    await failed(HTTPAttemptExecutor(planner, sender, timeout_seconds=0.005), code="timeout")
    sender.send.assert_not_awaited()
