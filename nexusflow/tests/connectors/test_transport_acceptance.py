"""Actual loopback TLS acceptance with a test-only public-pin dial redirection.

The production public IPv4 policy is never weakened. Only the test dial wrapper
maps the verified numeric pin to the disposable local TLS server.
"""

import asyncio
import contextlib
import socket
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import ApprovedHTTPDestination
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPTransportError

PRIVATE = "never-log-business-value"


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    directory = tmp_path_factory.mktemp("connector-tls")
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable connector CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api.example.com")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("api.example.com")]), False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = (directory / name for name in ("ca.pem", "cert.pem", "key.pem"))
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_file, cert_file, key_file


def client_context(certificates):
    context = ssl.create_default_context(cafile=str(certificates[0]))
    context.set_alpn_protocols(["http/1.1"])
    return context


@contextlib.asynccontextmanager
async def endpoint(certificates, response, *, stalled=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificates[1]), str(certificates[2]))
    context.set_alpn_protocols(["http/1.1"])
    names, received, writers, tasks = [], [], set(), set()
    arrived, disconnected = asyncio.Event(), asyncio.Event()
    context.set_servername_callback(lambda _socket, name, _context: names.append(name))

    async def handler(reader, writer):
        writers.add(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            size = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    size = int(line.split(b":", 1)[1])
            body = await reader.readexactly(size)
            received.append(head + body)
            arrived.set()
            if stalled:
                await reader.read(1)
                disconnected.set()
            else:
                writer.write(response)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            disconnected.set()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()
            writers.discard(writer)

    def connected(reader, writer):
        task = asyncio.create_task(handler(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(
        connected, "127.0.0.1", 0, ssl=context, ssl_handshake_timeout=1
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, names, received, arrived, disconnected
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def approved(port, *, host="api.example.com"):
    origin = f"https://{host}:{port}"
    return ApprovedHTTPDestination(
        scope=Scope(organization_id="organization", environment_id="dev"),
        connection_id="external-api",
        connection_revision="v1",
        definition_digest="sha256:" + "a" * 64,
        operation_id="run-1-step-1",
        url=origin + "/business?private=" + PRIVATE,
        origin=origin,
        host=host,
        port=port,
        ipv4_addresses=("8.8.8.8", "9.9.9.9"),
    )


def redirect_dial(monkeypatch, *, expected_host="api.example.com"):
    original = asyncio.open_connection
    calls = []

    async def dial(*args, **kwargs):
        assert not args
        assert kwargs["host"] == "8.8.8.8"
        assert kwargs["family"] == socket.AF_INET
        assert kwargs["server_hostname"] == expected_host
        assert kwargs["ssl"].check_hostname
        assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
        calls.append(dict(kwargs))
        kwargs["host"] = "127.0.0.1"
        return await original(**kwargs)

    async def no_dns(*args, **kwargs):
        raise AssertionError("The transport must dial numeric pins without DNS")

    monkeypatch.setattr(asyncio, "open_connection", dial)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", no_dns)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:1")
    return calls


@pytest.mark.asyncio
async def test_real_tls_uses_original_hostname_and_host_with_only_first_public_pin(
    certificates, monkeypatch
):
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
    async with endpoint(certificates, response) as (port, names, received, _, _):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(ssl_context=client_context(certificates))
        plan = approved(port)
        request = HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
        result = await transport.send(plan, request)
        assert result.operation_id == plan.operation_id
        assert result.status_code == 200 and result.body == b"{}"
        assert names == ["api.example.com"]
        assert len(calls) == 1
        assert len(received) == 1 and received[0].endswith(PRIVATE.encode())
        assert received[0].startswith(
            ("POST /business?private=" + PRIVATE + " HTTP/1.1\r\n").encode()
        )
        assert f"host: api.example.com:{port}" in received[0].decode().lower()
        assert b"accept-encoding: identity" in received[0].lower()
        assert b"authorization:" not in received[0].lower()
        assert b"cookie:" not in received[0].lower()
        assert PRIVATE not in repr(plan) + repr(request) + repr(result)


@pytest.mark.asyncio
async def test_real_tls_chunked_response_and_trailers_are_framed_without_leaks(
    certificates, monkeypatch
):
    body = PRIVATE.encode()
    response = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(body):x}\r\n".encode()
        + body
        + b"\r\n0\r\nX-Private: "
        + body
        + b"\r\n\r\n"
    )
    async with endpoint(certificates, response) as (port, _, received, _, _):
        calls = redirect_dial(monkeypatch)
        result = await PinnedHTTPTransport(ssl_context=client_context(certificates)).send(
            approved(port), HTTPAttemptRequest()
        )
        assert result.body == body and len(received) == len(calls) == 1
        assert PRIVATE not in repr(result)
        assert all(name.lower() != "x-private" for name, _ in result.headers)


@pytest.mark.asyncio
async def test_real_tls_redirect_is_returned_without_following(certificates, monkeypatch):
    location = "https://other.example.com/" + PRIVATE
    response = (
        "HTTP/1.1 302 Found\r\nLocation: " + location + "\r\nContent-Length: 0\r\n\r\n"
    ).encode()
    async with endpoint(certificates, response) as (port, _, received, _, _):
        calls = redirect_dial(monkeypatch)
        result = await PinnedHTTPTransport(ssl_context=client_context(certificates)).send(
            approved(port), HTTPAttemptRequest()
        )
        assert result.status_code == 302
        assert dict(result.headers)["location"] == location
        assert len(calls) == len(received) == 1
        assert PRIVATE not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["untrusted", "wrong-host"])
async def test_tls_verification_rejects_before_business_payload(certificates, monkeypatch, failure):
    async with endpoint(certificates, b"") as (port, _, received, _, _):
        host = "wrong.example.com" if failure == "wrong-host" else "api.example.com"
        calls = redirect_dial(monkeypatch, expected_host=host)
        context = (
            client_context(certificates)
            if failure == "wrong-host"
            else ssl.create_default_context()
        )
        transport = PinnedHTTPTransport(ssl_context=context)
        with pytest.raises(HTTPTransportError) as caught:
            await transport.send(
                approved(port, host=host), HTTPAttemptRequest(method="POST", body=PRIVATE.encode())
            )
        assert caught.value.code == "unavailable"
        assert not caught.value.delivery_may_have_occurred
        assert not caught.value.retryable
        assert len(calls) == 1 and received == []
        assert PRIVATE not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, limit, code",
    [
        (b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nx", 1024, "protocol_error"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello", 4, "response_too_large"),
        (
            b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 1\r\n\r\nx",
            1024,
            "protocol_error",
        ),
    ],
)
async def test_real_tls_framing_and_body_limits_are_nonretryable(
    certificates, monkeypatch, response, limit, code
):
    async with endpoint(certificates, response) as (port, _, received, _, _):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(
            ssl_context=client_context(certificates), max_response_bytes=limit
        )
        with pytest.raises(HTTPTransportError) as caught:
            await transport.send(approved(port), HTTPAttemptRequest())
        assert caught.value.code == code
        assert caught.value.delivery_may_have_occurred and not caught.value.retryable
        assert len(calls) == len(received) == 1


@pytest.mark.asyncio
async def test_real_tls_timeout_closes_connection_and_marks_possible_delivery(
    certificates, monkeypatch
):
    async with endpoint(certificates, b"", stalled=True) as (port, _, _, arrived, disconnected):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(ssl_context=client_context(certificates), timeout_seconds=1)
        with pytest.raises(HTTPTransportError) as caught:
            await transport.send(approved(port), HTTPAttemptRequest())
        assert arrived.is_set()
        assert caught.value.code == "timeout" and caught.value.delivery_may_have_occurred
        assert not caught.value.retryable and len(calls) == 1
        await asyncio.wait_for(disconnected.wait(), 2)


@pytest.mark.asyncio
async def test_real_tls_caller_cancellation_closes_connection(certificates, monkeypatch):
    async with endpoint(certificates, b"", stalled=True) as (port, _, _, arrived, disconnected):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(ssl_context=client_context(certificates))
        task = asyncio.create_task(transport.send(approved(port), HTTPAttemptRequest()))
        await asyncio.wait_for(arrived.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1
        await asyncio.wait_for(disconnected.wait(), 2)


def test_unverified_context_is_rejected(certificates):
    context = client_context(certificates)
    context.check_hostname = False
    with pytest.raises(ValueError):
        PinnedHTTPTransport(ssl_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, header_limit, code",
    [
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nx",
            16384,
            "protocol_error",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            16384,
            "protocol_error",
        ),
        (
            b"HTTP/1.1 200 OK\r\nX-Oversized: " + b"x" * 1100 + b"\r\nContent-Length: 0\r\n\r\n",
            1024,
            "response_too_large",
        ),
        (
            b"HTTP/1.1 101 Switching Protocols\r\nConnection: upgrade\r\nUpgrade: websocket\r\n\r\n",
            16384,
            "protocol_error",
        ),
        (
            b"HTTP/1.1 103 Early Hints\r\n\r\n" * 6
            + b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
            16384,
            "protocol_error",
        ),
    ],
)
async def test_real_tls_rejects_ambiguous_or_excessive_wire_metadata(
    certificates, monkeypatch, response, header_limit, code
):
    async with endpoint(certificates, response) as (port, _, received, _, _):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(
            ssl_context=client_context(certificates), max_header_bytes=header_limit
        )
        with pytest.raises(HTTPTransportError) as caught:
            await transport.send(approved(port), HTTPAttemptRequest())
        assert caught.value.code == code
        assert caught.value.delivery_may_have_occurred
        assert not caught.value.retryable
        assert len(received) == len(calls) == 1


@pytest.mark.asyncio
async def test_real_tls_head_returns_no_body_without_applying_length_as_payload_limit(
    certificates, monkeypatch
):
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 999999\r\nConnection: close\r\n\r\n"
    async with endpoint(certificates, response) as (port, _, received, _, _):
        calls = redirect_dial(monkeypatch)
        transport = PinnedHTTPTransport(
            ssl_context=client_context(certificates), max_response_bytes=0
        )
        result = await transport.send(approved(port), HTTPAttemptRequest(method="HEAD"))
        assert result.status_code == 200 and result.body == b""
        assert len(received) == len(calls) == 1
