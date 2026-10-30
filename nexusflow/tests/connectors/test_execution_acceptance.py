"""Independent real-registry/coordinator/TLS acceptance with no public network.

Only the test dial maps an asserted public IPv4 pin to a disposable TLS endpoint;
production destination policy, certificate verification and SNI remain intact.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.execution import HTTPAttemptExecutor
from nexusflow.modules.connectors.execution_types import HTTPExecutionError
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.tests.connectors import test_transport_acceptance as tls
from nexusflow.tests.connectors.test_definitions import definition

certificates = tls.certificates
SCOPE = Scope(organization_id="organization", environment_id="dev")
PRIVATE = "private-execution-business-value"
REFERENCE = "opaque-credential-reference"
SUCCESS = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"


class Authorization:
    def __init__(self, events):
        self.events = events
        self.denied = False

    def authorize(self, scope, permission, resource_id):
        self.events.append("authorize")
        assert permission == "worker.execute" and resource_id == "external-api"
        if self.denied or scope != SCOPE:
            raise PermissionError(PRIVATE)
        return "worker-1"


class DNS:
    def __init__(self, events):
        self.events = events
        self.after_resolution = lambda: None

    async def resolve_ipv4(self, host):
        self.events.append("dns")
        assert host == "api.example.com"
        self.after_resolution()
        return ("8.8.8.8", "9.9.9.9")


@contextlib.contextmanager
def worker(tmp_path, monkeypatch, *, port=443, secret=None):
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    origin = "https://api.example.com" + (f":{port}" if port != 443 else "")
    repository.put(
        SCOPE,
        "external-api",
        "v1",
        definition(base_origin=origin, allowed_origins=[origin], secret_reference=secret),
        "administrator",
    )
    events = []
    authorization, dns = Authorization(events), DNS(events)
    original_get = repository.get

    def get(*args):
        events.append("get")
        return original_get(*args)

    monkeypatch.setattr(repository, "get", get)
    planner = HTTPDestinationPlanner(repository, authorization, dns)
    destination = HTTPDestinationRequest(
        connection_id="external-api",
        connection_revision="v1",
        operation_id="run-1-step-1",
        path="/business?private=" + PRIVATE,
    )
    try:
        yield planner, repository, authorization, dns, destination, events
    finally:
        repository.close()


def executor(planner, certificates, **options):
    return HTTPAttemptExecutor(
        planner,
        PinnedHTTPTransport(ssl_context=tls.client_context(certificates)),
        **options,
    )


def private_failure(error, code, *, transmitted=False):
    assert error.code == code
    assert error.delivery_may_have_occurred is transmitted
    assert error.retryable is False
    assert PRIVATE not in str(error) + repr(error)
    assert REFERENCE not in str(error) + repr(error)


def checked_dial(monkeypatch, events, *, after_handshake=lambda: None):
    calls = tls.redirect_dial(monkeypatch)
    original = asyncio.open_connection

    async def dial(*args, **kwargs):
        reader, writer = await original(*args, **kwargs)
        events.append("tls")
        after_handshake()
        original_write = writer.write

        def write(data):
            events.append("write")
            original_write(data)

        monkeypatch.setattr(writer, "write", write)
        return reader, writer

    monkeypatch.setattr(asyncio, "open_connection", dial)
    return calls


@pytest.mark.asyncio
async def test_actual_registry_dns_tls_and_three_authorization_boundaries(
    tmp_path, monkeypatch, certificates
):
    async with tls.endpoint(certificates, SUCCESS) as (port, names, received, _, _):
        with worker(tmp_path, monkeypatch, port=port) as (planner, _, _, _, destination, events):
            calls = checked_dial(monkeypatch, events)
            request = HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
            result = await executor(planner, certificates).execute(SCOPE, destination, request)
            assert events == [
                "authorize",
                "get",
                "dns",
                "authorize",
                "get",
                "tls",
                "authorize",
                "get",
                "write",
            ]
            assert result.operation_id == destination.operation_id
            assert result.status_code == 200 and result.body == b"{}"
            assert names == ["api.example.com"]
            assert len(calls) == len(received) == 1
            assert received[0].endswith(PRIVATE.encode())
            assert f"host: api.example.com:{port}" in received[0].decode().lower()
            assert b"authorization:" not in received[0].lower()
            assert PRIVATE not in repr(destination) + repr(request) + repr(result)


@pytest.mark.asyncio
async def test_secret_reference_blocks_before_dns_or_socket(tmp_path, monkeypatch, certificates):
    forbidden_dial = AsyncMock(side_effect=AssertionError("No socket may be opened"))
    monkeypatch.setattr(asyncio, "open_connection", forbidden_dial)
    with worker(tmp_path, monkeypatch, secret=REFERENCE) as (planner, _, _, _, destination, events):
        with pytest.raises(HTTPExecutionError) as caught:
            await executor(planner, certificates).execute(SCOPE, destination, HTTPAttemptRequest())
        private_failure(caught.value, "credentials_required")
        assert events == ["authorize", "get"]
        forbidden_dial.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_dns_blocks_before_socket(tmp_path, monkeypatch, certificates):
    forbidden_dial = AsyncMock(side_effect=AssertionError("No socket may be opened"))
    monkeypatch.setattr(asyncio, "open_connection", forbidden_dial)
    with worker(tmp_path, monkeypatch) as (planner, _, auth, dns, destination, events):
        dns.after_resolution = lambda: setattr(auth, "denied", True)
        with pytest.raises(HTTPExecutionError) as caught:
            await executor(planner, certificates).execute(SCOPE, destination, HTTPAttemptRequest())
        private_failure(caught.value, "forbidden")
        assert events == ["authorize", "get", "dns", "authorize"]
        forbidden_dial.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "corrupt"])
async def test_change_during_tls_is_rechecked_before_any_business_write(
    tmp_path, monkeypatch, certificates, change
):
    async with tls.endpoint(certificates, SUCCESS) as (port, names, received, _, disconnected):
        with worker(tmp_path, monkeypatch, port=port) as (
            planner,
            repository,
            auth,
            _,
            destination,
            events,
        ):

            def change_state():
                if change == "revoke":
                    auth.denied = True
                else:
                    repository._db.execute(
                        "UPDATE http_connections SET definition_json=?", ("{" + PRIVATE,)
                    )
                    repository._db.commit()

            calls = checked_dial(monkeypatch, events, after_handshake=change_state)
            with pytest.raises(HTTPExecutionError) as caught:
                await executor(planner, certificates).execute(
                    SCOPE, destination, HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
                )
            private_failure(caught.value, "forbidden" if change == "revoke" else "unavailable")
            assert "write" not in events and received == []
            assert names == ["api.example.com"] and len(calls) == 1
            await asyncio.wait_for(disconnected.wait(), 2)


@pytest.mark.asyncio
async def test_cross_scope_permission_denied_before_private_read(
    tmp_path, monkeypatch, certificates
):
    forbidden_dial = AsyncMock(side_effect=AssertionError("No socket may be opened"))
    monkeypatch.setattr(asyncio, "open_connection", forbidden_dial)
    with worker(tmp_path, monkeypatch) as (planner, _, _, _, destination, events):
        other = Scope(organization_id=SCOPE.organization_id, environment_id="prod")
        with pytest.raises(HTTPExecutionError) as caught:
            await executor(planner, certificates).execute(other, destination, HTTPAttemptRequest())
        private_failure(caught.value, "forbidden")
        assert events == ["authorize"]
        forbidden_dial.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_method_body_is_rejected_before_authorization_or_dns(
    tmp_path, monkeypatch, certificates
):
    forbidden_dial = AsyncMock(side_effect=AssertionError("No socket may be opened"))
    monkeypatch.setattr(asyncio, "open_connection", forbidden_dial)
    with worker(tmp_path, monkeypatch) as (planner, _, _, _, destination, events):
        with pytest.raises(HTTPExecutionError) as caught:
            await executor(planner, certificates).execute(
                SCOPE, destination, HTTPAttemptRequest(method="GET", body=PRIVATE.encode())
            )
        private_failure(caught.value, "invalid_request")
        assert events == []
        forbidden_dial.assert_not_awaited()


@pytest.mark.asyncio
async def test_redirect_response_returns_without_second_attempt(
    tmp_path, monkeypatch, certificates
):
    location = "https://other.example.com/" + PRIVATE
    response = (
        "HTTP/1.1 307 Temporary Redirect\r\nLocation: " + location + "\r\nContent-Length: 0\r\n\r\n"
    ).encode()
    async with tls.endpoint(certificates, response) as (port, _, received, _, _):
        with worker(tmp_path, monkeypatch, port=port) as (planner, _, _, _, destination, events):
            calls = checked_dial(monkeypatch, events)
            result = await executor(planner, certificates).execute(
                SCOPE, destination, HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
            )
            assert result.status_code == 307 and dict(result.headers)["location"] == location
            assert len(calls) == len(received) == events.count("dns") == 1
            assert PRIVATE not in repr(result)


@pytest.mark.asyncio
async def test_failure_after_write_preserves_delivery_uncertainty(
    tmp_path, monkeypatch, certificates
):
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nx"
    async with tls.endpoint(certificates, response) as (port, _, received, _, _):
        with worker(tmp_path, monkeypatch, port=port) as (planner, _, _, _, destination, events):
            calls = checked_dial(monkeypatch, events)
            with pytest.raises(HTTPExecutionError) as caught:
                await executor(planner, certificates).execute(
                    SCOPE, destination, HTTPAttemptRequest()
                )
            private_failure(caught.value, "protocol_error", transmitted=True)
            assert len(calls) == len(received) == 1


@pytest.mark.asyncio
async def test_total_timeout_after_write_closes_actual_tls(tmp_path, monkeypatch, certificates):
    async with tls.endpoint(certificates, b"", stalled=True) as (
        port,
        _,
        received,
        arrived,
        disconnected,
    ):
        with worker(tmp_path, monkeypatch, port=port) as (planner, _, _, _, destination, events):
            calls = checked_dial(monkeypatch, events)
            with pytest.raises(HTTPExecutionError) as caught:
                await executor(planner, certificates, timeout_seconds=1).execute(
                    SCOPE, destination, HTTPAttemptRequest()
                )
            private_failure(caught.value, "timeout", transmitted=True)
            assert arrived.is_set() and len(calls) == len(received) == 1
            await asyncio.wait_for(disconnected.wait(), 2)


@pytest.mark.asyncio
async def test_cancellation_after_write_closes_actual_tls(tmp_path, monkeypatch, certificates):
    async with tls.endpoint(certificates, b"", stalled=True) as (
        port,
        _,
        received,
        arrived,
        disconnected,
    ):
        with worker(tmp_path, monkeypatch, port=port) as (planner, _, _, _, destination, events):
            calls = checked_dial(monkeypatch, events)
            task = asyncio.create_task(
                executor(planner, certificates).execute(SCOPE, destination, HTTPAttemptRequest())
            )
            await asyncio.wait_for(arrived.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == len(received) == 1
            await asyncio.wait_for(disconnected.wait(), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["dns", "dial"])
async def test_cancellation_before_sender_write_has_no_business_payload(
    tmp_path, monkeypatch, certificates, phase
):
    entered = asyncio.Event()

    async def stall(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with worker(tmp_path, monkeypatch) as (planner, _, _, dns, destination, events):
        if phase == "dns":
            monkeypatch.setattr(dns, "resolve_ipv4", stall)
        dial = AsyncMock(side_effect=stall)
        monkeypatch.setattr(asyncio, "open_connection", dial)
        task = asyncio.create_task(
            executor(planner, certificates).execute(SCOPE, destination, HTTPAttemptRequest())
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "write" not in events
        assert dial.await_count == (phase == "dial")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["dns", "dial"])
async def test_total_deadline_spans_dns_and_dial_before_business_write(
    tmp_path, monkeypatch, certificates, phase
):
    entered = asyncio.Event()

    async def stall(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with worker(tmp_path, monkeypatch) as (planner, _, _, dns, destination, events):
        if phase == "dns":
            monkeypatch.setattr(dns, "resolve_ipv4", stall)
        dial = AsyncMock(side_effect=stall)
        monkeypatch.setattr(asyncio, "open_connection", dial)
        with pytest.raises(HTTPExecutionError) as caught:
            await executor(planner, certificates, timeout_seconds=0.05).execute(
                SCOPE, destination, HTTPAttemptRequest()
            )
        private_failure(caught.value, "timeout", transmitted=phase == "dial")
        assert entered.is_set() and "write" not in events
        assert dial.await_count == (phase == "dial")
