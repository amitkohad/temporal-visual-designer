"""Internal credential-free single-attempt transport contracts; not public APIs."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.modules.connectors.destination_types import ApprovedHTTPDestination


@dataclass(frozen=True)
class HTTPAttemptRequest:
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] = "GET"
    body: bytes = field(default=b"", repr=False)
    content_type: Literal["application/json", "text/plain", "application/octet-stream"] = (
        "application/json"
    )


@dataclass(frozen=True)
class HTTPAttemptResult:
    operation_id: str
    status_code: int
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)


class PinnedHTTPSender(Protocol):
    async def send(
        self, destination: ApprovedHTTPDestination, request: HTTPAttemptRequest
    ) -> HTTPAttemptResult: ...


HTTPTransportErrorCode = Literal[
    "invalid_request", "unavailable", "timeout", "protocol_error", "response_too_large"
]


class HTTPTransportError(Exception):
    def __init__(
        self, code: HTTPTransportErrorCode, *, delivery_may_have_occurred: bool = False
    ) -> None:
        self.code: HTTPTransportErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPTransportErrorCode)
            else "unavailable"
        )
        self.delivery_may_have_occurred = (
            delivery_may_have_occurred if type(delivery_may_have_occurred) is bool else True
        )
        self.retryable = False
        super().__init__("HTTP transport attempt failed")
