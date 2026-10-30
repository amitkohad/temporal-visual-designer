"""One credential-free HTTPS attempt using a freshly authorized internal routing plan."""

import asyncio
import inspect
import math
import socket
import ssl
from collections.abc import Callable
from urllib.parse import urlsplit

import h11
from pydantic import TypeAdapter

from nexusflow.contracts.models import Digest, Identifier, Scope
from nexusflow.modules.connectors.attempt_policy import checked_attempt
from nexusflow.modules.connectors.destination_types import ApprovedHTTPDestination
from nexusflow.modules.connectors.policy import checked_ipv4, checked_url
from nexusflow.modules.connectors.transport_types import (
    HTTPAttemptRequest,
    HTTPAttemptResult,
    HTTPTransportError,
    HTTPTransportErrorCode,
)


class PinnedHTTPTransport:
    """Internal adapter, not an authorization boundary or a retry/delivery journal.

    The trusted caller must obtain a fresh planner approval before every attempt.
    A dataclass plan is not a cryptographic capability. Redirects are returned, never
    followed; the next attempt needs a new ``prepare_redirect`` approval.
    Cleanup closes immediately and permits up to 0.5 additional seconds for TLS
    shutdown after the total attempt deadline; cancellation aborts immediately.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        max_request_bytes: int = 1048576,
        max_response_bytes: int = 1048576,
        max_header_bytes: int = 16384,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60
            or any(
                type(limit) is not int or not 0 <= limit <= 4194304
                for limit in (max_request_bytes, max_response_bytes)
            )
            or type(max_header_bytes) is not int
            or not 1024 <= max_header_bytes <= 65536
        ):
            raise ValueError("Invalid HTTP transport limits")
        context = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._check_tls(context)
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_header_bytes = max_header_bytes
        self.ssl_context = context

    @staticmethod
    def _check_tls(context: ssl.SSLContext) -> None:
        if (
            not isinstance(context, ssl.SSLContext)
            or context.verify_mode != ssl.CERT_REQUIRED
            or not context.check_hostname
        ):
            raise ValueError("Verified HTTPS context required")

    def _validate(self, destination: ApprovedHTTPDestination, request: HTTPAttemptRequest) -> None:
        try:
            self._check_tls(self.ssl_context)
            if (
                type(destination) is not ApprovedHTTPDestination
                or type(request) is not HTTPAttemptRequest
            ):
                raise ValueError
            if type(destination.scope) is not Scope:
                raise ValueError
            Scope.model_validate(destination.scope.model_dump(), strict=True)
            for value in (
                destination.connection_id,
                destination.connection_revision,
                destination.operation_id,
            ):
                TypeAdapter(Identifier).validate_python(value, strict=True)
            TypeAdapter(Digest).validate_python(destination.definition_digest, strict=True)
            if (
                type(destination.port) is not int
                or type(destination.redirect_count) is not int
                or not 0 <= destination.redirect_count <= 10
                or type(destination.origin) is not str
                or type(destination.host) is not str
                or checked_url(destination.url, (destination.origin,))
                != (destination.url, destination.origin, destination.host, destination.port)
                or checked_ipv4(destination.ipv4_addresses) != destination.ipv4_addresses
            ):
                raise ValueError
        except Exception:
            raise HTTPTransportError("invalid_request") from None

    @staticmethod
    def _deadline(deadline: asyncio.Timeout, cancellation_count: int) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > cancellation_count:
            raise asyncio.CancelledError
        expires = deadline.when()
        if deadline.expired() or (
            expires is not None and asyncio.get_running_loop().time() >= expires
        ):
            raise TimeoutError

    async def _receive(
        self,
        reader: asyncio.StreamReader,
        connection: h11.Connection,
        destination: ApprovedHTTPDestination,
        request: HTTPAttemptRequest,
        deadline: asyncio.Timeout,
        cancellation_count: int,
    ) -> HTTPAttemptResult:
        status: int | None = None
        headers: tuple[tuple[str, str], ...] = ()
        body = bytearray()
        metadata_bytes = 0
        header_count = 0
        informational = 0
        eof = False
        while True:
            self._deadline(deadline, cancellation_count)
            before = len(connection.trailing_data[0])
            event = connection.next_event()
            consumed = before - len(connection.trailing_data[0])
            metadata_bytes += consumed - (len(event.data) if isinstance(event, h11.Data) else 0)
            if metadata_bytes > self.max_header_bytes:
                raise HTTPTransportError("response_too_large")
            if event is h11.NEED_DATA:
                if eof:
                    raise HTTPTransportError("protocol_error")
                chunk = await reader.read(min(16384, self.max_header_bytes + 1))
                self._deadline(deadline, cancellation_count)
                eof = not chunk
                connection.receive_data(chunk)
            elif isinstance(event, (h11.Response, h11.InformationalResponse, h11.EndOfMessage)):
                if isinstance(event, (h11.Response, h11.InformationalResponse)) and (
                    event.http_version not in (b"1.0", b"1.1")
                ):
                    raise HTTPTransportError("protocol_error")
                header_count += len(event.headers)
                if header_count > 100:
                    raise HTTPTransportError("response_too_large")
                if isinstance(event, h11.InformationalResponse):
                    informational += 1
                    if event.status_code == 101 or informational > 5:
                        raise HTTPTransportError("protocol_error")
                elif isinstance(event, h11.Response):
                    if not 200 <= event.status_code <= 599 or status is not None:
                        raise HTTPTransportError("protocol_error")
                    status = event.status_code
                    headers = tuple(
                        (k.decode("ascii"), v.decode("latin-1")) for k, v in event.headers
                    )
                    names = {key for key, _ in headers}
                    if {"transfer-encoding", "content-length"} <= names:
                        raise HTTPTransportError("protocol_error")
                    for key, value in headers:
                        if key == "content-encoding" and value.lower() != "identity":
                            raise HTTPTransportError("protocol_error")
                        if key == "transfer-encoding" and value.lower() != "chunked":
                            raise HTTPTransportError("protocol_error")
                        if (
                            key == "content-length"
                            and request.method != "HEAD"
                            and int(value) > self.max_response_bytes
                        ):
                            raise HTTPTransportError("response_too_large")
                else:
                    if status is None:
                        raise HTTPTransportError("protocol_error")
                    self._deadline(deadline, cancellation_count)
                    return HTTPAttemptResult(destination.operation_id, status, headers, bytes(body))
            elif isinstance(event, h11.Data):
                if len(body) + len(event.data) > self.max_response_bytes:
                    raise HTTPTransportError("response_too_large")
                body.extend(event.data)
            else:
                raise HTTPTransportError("protocol_error")

    async def send(
        self,
        destination: ApprovedHTTPDestination,
        request: HTTPAttemptRequest,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> HTTPAttemptResult:
        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        request = checked_attempt(request, self.max_request_bytes)
        self._validate(destination, request)
        if before_write is not None and not callable(before_write):
            raise HTTPTransportError("invalid_request")
        writer: asyncio.StreamWriter | None = None
        transmitted = False
        cancelled = False
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                reader, writer = await asyncio.open_connection(
                    host=destination.ipv4_addresses[0],
                    port=destination.port,
                    family=socket.AF_INET,
                    ssl=self.ssl_context,
                    server_hostname=destination.host,
                    ssl_handshake_timeout=self.timeout_seconds,
                    ssl_shutdown_timeout=0.5,
                    limit=self.max_header_bytes,
                )
                self._deadline(deadline, cancellation_count)
                tls = writer.get_extra_info("ssl_object")
                if tls is None or tls.selected_alpn_protocol() not in (None, "http/1.1"):
                    raise HTTPTransportError("protocol_error")
                connection = h11.Connection(
                    h11.CLIENT, max_incomplete_event_size=self.max_header_bytes
                )
                parsed = urlsplit(destination.url)
                target = parsed.path + ("?" + parsed.query if parsed.query else "")
                authority = destination.host + (
                    f":{destination.port}" if destination.port != 443 else ""
                )
                head = connection.send(
                    h11.Request(
                        method=request.method,
                        target=target,
                        headers=[
                            ("Host", authority),
                            ("Connection", "close"),
                            ("Accept-Encoding", "identity"),
                            ("Content-Type", request.content_type),
                            ("Content-Length", str(len(request.body))),
                        ],
                    )
                )
                payload = head or b""
                if request.body:
                    payload += connection.send(h11.Data(data=request.body)) or b""
                payload += connection.send(h11.EndOfMessage()) or b""
                self._deadline(deadline, cancellation_count)
                if before_write is not None:
                    outcome = before_write()
                    if outcome is not None:
                        if inspect.iscoroutine(outcome):
                            outcome.close()
                        raise HTTPTransportError("invalid_request")
                    self._deadline(deadline, cancellation_count)
                transmitted = True
                writer.write(payload)
                await writer.drain()
                self._deadline(deadline, cancellation_count)
                return await self._receive(
                    reader, connection, destination, request, deadline, cancellation_count
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except HTTPTransportError as error:
            raise HTTPTransportError(error.code, delivery_may_have_occurred=transmitted) from None
        except TimeoutError:
            raise HTTPTransportError("timeout", delivery_may_have_occurred=transmitted) from None
        except h11.ProtocolError as error:
            code: HTTPTransportErrorCode = (
                "response_too_large" if error.error_status_hint == 431 else "protocol_error"
            )
            raise HTTPTransportError(code, delivery_may_have_occurred=transmitted) from None
        except Exception:
            raise HTTPTransportError(
                "unavailable", delivery_may_have_occurred=transmitted
            ) from None
        finally:
            if writer is not None:
                try:
                    writer.close()
                    if cancelled:
                        writer.transport.abort()
                    else:
                        async with asyncio.timeout(0.5):
                            await writer.wait_closed()
                            if task is not None and task.cancelling() > cancellation_count:
                                raise asyncio.CancelledError
                except asyncio.CancelledError:
                    try:
                        writer.transport.abort()
                    except Exception:
                        pass
                    raise
                except Exception:
                    try:
                        writer.transport.abort()
                    except Exception:
                        pass
