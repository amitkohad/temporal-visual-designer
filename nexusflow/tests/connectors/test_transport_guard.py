"""The synchronous execution guard runs after TLS and before any request write."""

import asyncio
import time

import pytest

from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPTransportError
from nexusflow.tests.connectors.test_transport import PLAN
from nexusflow.tests.connectors.test_transport import wire as wire


@pytest.mark.asyncio
async def test_guard_runs_once_after_dial_before_first_write(wire):
    calls = []

    def guard():
        assert wire[2].await_count == 1
        assert wire[1].sent == []
        calls.append("authorized")

    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    assert result.status_code == 200 and calls == ["authorized"]
    assert len(wire[1].sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["forbidden", "credentials_required", "blocked_destination"])
async def test_guard_denial_preserves_code_without_transmitting(wire, code):
    def guard():
        raise HTTPTransportError(code)

    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    assert caught.value.code == code and not caught.value.delivery_may_have_occurred
    assert wire[1].sent == [] and wire[1].closed


@pytest.mark.asyncio
async def test_unknown_guard_errors_do_not_leak_provider_text(wire):
    def guard():
        raise RuntimeError("private-guard-secret")

    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    assert caught.value.code == "unavailable" and not caught.value.delivery_may_have_occurred
    assert "private-guard-secret" not in repr(caught.value) + str(caught.value)
    assert wire[1].sent == [] and wire[1].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [False, 1, "private-guard-secret"])
async def test_noncallable_guard_is_rejected_before_dial(wire, guard):
    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    assert caught.value.code == "invalid_request"
    assert not caught.value.delivery_may_have_occurred and wire[2].await_count == 0


@pytest.mark.asyncio
async def test_async_guard_is_not_accepted_or_transmitted(wire):
    async def guard():
        raise AssertionError("An asynchronous guard must never be awaited")

    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    assert caught.value.code == "invalid_request" and wire[1].sent == []
    assert not caught.value.delivery_may_have_occurred and wire[1].closed


@pytest.mark.asyncio
async def test_guard_cancellation_closes_before_write(wire):
    def guard():
        asyncio.current_task().cancel()

    task = asyncio.create_task(
        PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest(), before_write=guard)
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire[1].sent == [] and wire[1].closed and wire[1].aborted


@pytest.mark.asyncio
async def test_synchronous_guard_cannot_approve_after_deadline(wire):
    def guard():
        time.sleep(0.02)

    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport(timeout_seconds=0.005).send(
            PLAN, HTTPAttemptRequest(), before_write=guard
        )
    assert caught.value.code == "timeout" and not caught.value.delivery_may_have_occurred
    assert wire[1].sent == [] and wire[1].closed


@pytest.mark.asyncio
async def test_request_is_snapshotted_before_awaiting_dial(wire):
    request = HTTPAttemptRequest(method="POST", body=b"original")

    async def dial(**kwargs):
        object.__setattr__(request, "body", b"changed-during-dial")
        return wire[0], wire[1]

    wire[2].side_effect = dial
    await PinnedHTTPTransport().send(PLAN, request)
    assert wire[1].sent[0].endswith(b"original")
    assert b"changed-during-dial" not in wire[1].sent[0]
