"""Private SMTP send contracts; credentials never enter public diagnostics."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.modules.connectors.smtp_secret_types import SMTPLoginCredential


@dataclass(frozen=True)
class SMTPSendRequest:
    """Private send request; credential material stays off repr."""

    smtp_host: str = field(repr=False)
    port: int = field(repr=False)
    envelope_sender: str = field(repr=False)
    recipients: tuple[str, ...] = field(repr=False)
    data: bytes = field(repr=False)
    credential: SMTPLoginCredential | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SMTPSendResult:
    operation_id: str
    submission_status: Literal["accepted"] = "accepted"


SMTPTransportErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "credentials_required",
    "unavailable",
    "timeout",
    "delivery_uncertain",
]


class SMTPTransportError(Exception):
    def __init__(
        self,
        code: SMTPTransportErrorCode,
        *,
        delivery_may_have_occurred: bool = False,
    ) -> None:
        self.code: SMTPTransportErrorCode = (
            code
            if type(code) is str and code in get_args(SMTPTransportErrorCode)
            else "unavailable"
        )
        self.retryable = False
        self.delivery_may_have_occurred = bool(delivery_may_have_occurred)
        super().__init__("SMTP transport failed")


class PinnedSMTPSender(Protocol):
    def send(
        self,
        operation_id: str,
        request: SMTPSendRequest,
        *,
        before_write=None,
        timeout_seconds: float = 30,
    ) -> SMTPSendResult: ...
