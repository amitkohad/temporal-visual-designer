"""Wire-policy tests use injected streams, never actual DNS or network sockets."""

import asyncio
import math
import socket
import ssl
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import ApprovedHTTPDestination
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPTransportError

PRIVATE = "private-transport-sentinel"
PLAN = ApprovedHTTPDestination(
    scope=Scope(organization_id="acme", environment_id="dev"),
    connection_id="api",
    connection_revision="v1",
    definition_digest="sha256:" + "a" * 64,
    operation_id="run-1-step-1",
    url="https://api.example.com:8443/path?data=" + PRIVATE,
    origin="https://api.example.com:8443",
    host="api.example.com",
    port=8443,
    ipv4_addresses=("1.1.1.1", "8.8.8.8"),
)


class Reader:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, size):
        if not self.chunks:
            return b""
        value = self.chunks.pop(0)
        if isinstance(value, Exception):
            raise value
        if len(value) > size:
            self.chunks.insert(0, value[size:])
        return value[:size]


class Writer:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.aborted = False
        self.transport = self
        self.protocol = None
        self.drain = AsyncMock()
        self.wait_closed = AsyncMock()

    def get_extra_info(self, name):
        assert name == "ssl_object"
        return self

    def selected_alpn_protocol(self):
        return self.protocol

    def write(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


@pytest.fixture
def wire(monkeypatch):
    writer = Writer()
    reader = Reader([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"])
    dial = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", dial)
    return reader, writer, dial


async def failure(transport, wire, *, code=None, transmitted=True, plan=PLAN, request=None):
    with pytest.raises(HTTPTransportError) as caught:
        await transport.send(plan, request or HTTPAttemptRequest())
    error = caught.value
    if code:
        assert error.code == code
    assert error.delivery_may_have_occurred is transmitted
    assert error.retryable is False
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__
    if transmitted:
        assert wire[1].closed
    return error


@pytest.mark.asyncio
async def test_dials_first_numeric_pin_with_verified_original_tls_and_host(wire, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.invalid:9999")
    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert (result.operation_id, result.status_code, result.body) == (PLAN.operation_id, 200, b"OK")
    _, writer, dial = wire
    kwargs = dial.call_args.kwargs
    assert kwargs["host"] == "1.1.1.1" and kwargs["port"] == 8443
    assert kwargs["server_hostname"] == "api.example.com" and kwargs["family"] == socket.AF_INET
    assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED and kwargs["ssl"].check_hostname
    assert dial.await_count == 1 and writer.closed
    assert b"Host: api.example.com:8443\r\n" in writer.sent[0]
    assert b"GET /path?data=" + PRIVATE.encode() + b" HTTP/1.1" in writer.sent[0]
    assert b"Accept-Encoding: identity\r\n" in writer.sent[0]
    assert b"Connection: close\r\n" in writer.sent[0]
    assert PRIVATE not in repr(result) + repr(HTTPAttemptRequest(body=PRIVATE.encode()))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_body_is_sent_once_with_explicit_length(method, wire):
    request = HTTPAttemptRequest(method=method, body=b'{"a":1}', content_type="application/json")
    await PinnedHTTPTransport().send(PLAN, request)
    assert len(wire[1].sent) == 1
    assert b"Content-Length: 7\r\n" in wire[1].sent[0]
    assert wire[1].sent[0].endswith(b'{"a":1}')


@pytest.mark.asyncio
async def test_redirect_is_returned_without_following(wire):
    wire[0].chunks = [
        b"HTTP/1.1 302 Found\r\nLocation: https://other.invalid/"
        + PRIVATE.encode()
        + b"\r\nContent-Length: 0\r\n\r\n"
    ]
    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert result.status_code == 302 and len(wire[1].sent) == wire[2].await_count == 1
    assert ("location", "https://other.invalid/" + PRIVATE) in result.headers
    assert PRIVATE not in repr(result)


@pytest.mark.asyncio
async def test_split_chunked_body_and_checked_trailer(wire):
    wire[0].chunks = [
        b"HTTP/1.1 200",
        b" OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"3\r\nabc\r\n",
        b"2\r\nde\r\n0\r\nX-Trailer: private\r\n\r\n",
    ]
    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert result.body == b"abcde" and all(k != "x-trailer" for k, _ in result.headers)


@pytest.mark.asyncio
async def test_head_ignores_declared_representation_size(wire):
    wire[0].chunks = [b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n"]
    result = await PinnedHTTPTransport(max_response_bytes=0).send(
        PLAN, HTTPAttemptRequest(method="HEAD")
    )
    assert result.body == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nabc",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Encoding: gzip\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n",
        b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n",
        b"HTTP/1.1 600 bad\r\nContent-Length: 0\r\n\r\n",
        b"invalid " + PRIVATE.encode() + b"\r\n\r\n",
        b"",
    ],
)
async def test_bad_protocol_is_normalized(response, wire):
    wire[0].chunks = [response]
    await failure(PinnedHTTPTransport(), wire, code="protocol_error")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [b"2.0", b"9.9"])
async def test_unknown_response_http_version_is_rejected(version, wire):
    wire[0].chunks = [b"HTTP/" + version + b" 200 OK\r\nContent-Length: 0\r\n\r\n"]
    await failure(PinnedHTTPTransport(), wire, code="protocol_error")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n1234",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n1234\r\n0\r\n\r\n",
        b"HTTP/1.0 200 OK\r\n\r\n1234",
    ],
)
async def test_response_limit_in_all_framing_modes(response, wire):
    wire[0].chunks = [response]
    await failure(PinnedHTTPTransport(max_response_bytes=3), wire, code="response_too_large")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 200 OK\r\nX-Large: " + b"a" * 1100 + b"\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX-Large: " + b" " * 1100 + b"\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX-Large: "
        + b"a" * 1100
        + b"\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n" + b"X: a\r\n" * 101 + b"Content-Length: 0\r\n\r\n",
    ],
)
async def test_header_wire_and_count_limits(response, wire):
    wire[0].chunks = [response]
    await failure(PinnedHTTPTransport(max_header_bytes=1024), wire, code="response_too_large")


@pytest.mark.asyncio
async def test_informational_responses_are_bounded(wire):
    wire[0].chunks = [b"HTTP/1.1 100 Continue\r\n\r\n" * 6]
    await failure(PinnedHTTPTransport(), wire, code="protocol_error")


@pytest.mark.asyncio
async def test_cumulative_interim_wire_budget(wire):
    wire[0].chunks = [(b"HTTP/1.1 100 Continue\r\nX: " + b"a" * 300 + b"\r\n\r\n") * 4]
    await failure(PinnedHTTPTransport(max_header_bytes=1024), wire, code="response_too_large")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "other.invalid"),
        ("port", True),
        ("port", 443),
        ("origin", "https://other.invalid"),
        ("url", "http://api.example.com/a"),
        ("ipv4_addresses", ("127.0.0.1",)),
        ("ipv4_addresses", ("8.8.8.8", "1.1.1.1")),
        ("ipv4_addresses", ("1.1.1.1", "1.1.1.1")),
        ("scope", None),
        ("connection_id", "bad/id"),
        ("connection_revision", None),
        ("operation_id", True),
        ("definition_digest", "invalid"),
        ("redirect_count", True),
        ("redirect_count", 11),
    ],
)
async def test_malformed_plan_never_dials(field, value, wire):
    await failure(
        PinnedHTTPTransport(),
        wire,
        code="invalid_request",
        transmitted=False,
        plan=replace(PLAN, **{field: value}),
    )
    wire[2].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt",
    [
        HTTPAttemptRequest(method="get"),
        HTTPAttemptRequest(method="CONNECT"),
        HTTPAttemptRequest(method="HEAD", body=b"x"),
        HTTPAttemptRequest(body=b"x"),
        HTTPAttemptRequest(method="POST", body=bytearray(b"x")),
        HTTPAttemptRequest(content_type="text/plain\r\nAuthorization: " + PRIVATE),
        HTTPAttemptRequest(method="POST", body=b"1234"),
    ],
)
async def test_invalid_request_never_dials(attempt, wire):
    await failure(
        PinnedHTTPTransport(max_request_bytes=3),
        wire,
        code="invalid_request",
        transmitted=False,
        request=attempt,
    )
    wire[2].assert_not_awaited()


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"timeout_seconds": math.nan},
        {"timeout_seconds": math.inf},
        {"max_request_bytes": True},
        {"max_request_bytes": -1},
        {"max_request_bytes": 4194305},
        {"max_response_bytes": 4194305},
        {"max_response_bytes": 0.5},
        {"max_header_bytes": True},
        {"max_header_bytes": 1023},
        {"max_header_bytes": 65537},
    ],
)
def test_constructor_limits(options):
    with pytest.raises(ValueError):
        PinnedHTTPTransport(**options)


def test_insecure_tls_context_rejected():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError):
        PinnedHTTPTransport(ssl_context=context)


@pytest.mark.asyncio
async def test_mutated_tls_context_never_dials(wire):
    context = ssl.create_default_context()
    transport = PinnedHTTPTransport(ssl_context=context)
    context.check_hostname = False
    await failure(transport, wire, code="invalid_request", transmitted=False)
    wire[2].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["h2", "other"])
async def test_unsupported_alpn_does_not_transmit(protocol, wire):
    wire[1].protocol = protocol
    await failure(PinnedHTTPTransport(), wire, code="protocol_error", transmitted=False)
    assert wire[1].sent == [] and wire[1].closed


@pytest.mark.asyncio
async def test_missing_tls_does_not_transmit(wire):
    wire[1].get_extra_info = lambda _: None
    await failure(PinnedHTTPTransport(), wire, code="protocol_error", transmitted=False)
    assert wire[1].sent == [] and wire[1].closed


@pytest.mark.asyncio
async def test_connection_failure_no_retry_no_fallback(wire):
    wire[2].side_effect = OSError(PRIVATE)
    await failure(PinnedHTTPTransport(), wire, code="unavailable", transmitted=False)
    assert wire[2].await_count == 1


@pytest.mark.asyncio
async def test_write_failure_is_ambiguous(wire):
    def write(_):
        raise OSError(PRIVATE)

    wire[1].write = write
    await failure(PinnedHTTPTransport(), wire, code="unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["dial", "drain", "read"])
async def test_total_timeout_and_cleanup(phase, wire):
    async def stall(*args, **kwargs):
        await asyncio.Event().wait()

    if phase == "dial":
        wire[2].side_effect = stall
    elif phase == "drain":
        wire[1].drain.side_effect = stall
    else:
        wire[0].read = stall
    await failure(
        PinnedHTTPTransport(timeout_seconds=0.01), wire, code="timeout", transmitted=phase != "dial"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["dial", "drain", "read"])
async def test_caller_cancellation_propagates_and_closes(phase, wire):
    entered = asyncio.Event()

    async def stall(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    if phase == "dial":
        wire[2].side_effect = stall
    elif phase == "drain":
        wire[1].drain.side_effect = stall
    else:
        wire[0].read = stall
    task = asyncio.create_task(PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if phase != "dial":
        assert wire[1].closed and wire[1].aborted


@pytest.mark.asyncio
async def test_late_answer_after_suppressed_timeout_is_rejected(wire):
    async def late(size):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    wire[0].read = late
    await failure(PinnedHTTPTransport(timeout_seconds=0.01), wire, code="timeout")


@pytest.mark.asyncio
async def test_provider_read_exception_is_payload_free(wire):
    wire[0].chunks = [OSError(PRIVATE)]
    await failure(PinnedHTTPTransport(), wire, code="unavailable")


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_result(wire):
    wire[1].wait_closed.side_effect = OSError(PRIVATE)
    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert result.status_code == 200 and wire[1].closed and wire[1].aborted


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["dial", "drain", "read", "cleanup"])
async def test_suppressed_caller_cancellation_never_becomes_success(phase, wire):
    entered = asyncio.Event()

    async def suppress(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if phase == "dial":
                return wire[0], wire[1]
            if phase == "read":
                return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    if phase == "dial":
        wire[2].side_effect = suppress
    elif phase == "drain":
        wire[1].drain.side_effect = suppress
    elif phase == "read":
        wire[0].read = suppress
    else:
        wire[1].wait_closed.side_effect = suppress
    task = asyncio.create_task(PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire[1].closed and wire[1].aborted
    if phase == "dial":
        assert wire[1].sent == []


@pytest.mark.asyncio
async def test_close_and_abort_failures_cannot_expose_provider_exception(wire):
    def fail():
        raise OSError(PRIVATE)

    wire[1].close = fail
    wire[1].abort = fail
    result = await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert result.status_code == 200
    wire[0].chunks = [OSError(PRIVATE)]
    with pytest.raises(HTTPTransportError) as caught:
        await PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest())
    assert caught.value.code == "unavailable" and caught.value.delivery_may_have_occurred
    assert PRIVATE not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_cancel_during_cleanup_aborts_and_propagates(wire):
    entered = asyncio.Event()

    async def stall():
        entered.set()
        await asyncio.Event().wait()

    wire[1].wait_closed.side_effect = stall
    task = asyncio.create_task(PinnedHTTPTransport().send(PLAN, HTTPAttemptRequest()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire[1].closed and wire[1].aborted
