"""Private email preflight snapshots; these carry no sending authority."""

from dataclasses import dataclass, field
from typing import Literal, get_args

from nexusflow.contracts.models import Scope


@dataclass(frozen=True)
class PreparedEmailEnvelope:
    scope: Scope
    operation_id: str
    connection_id: str
    connection_revision: str
    definition_digest: str
    execution_mode: Literal["live"]
    smtp_host: str = field(repr=False)
    port: int = field(repr=False)
    sender: str = field(repr=False)
    to: tuple[str, ...] = field(repr=False)
    cc: tuple[str, ...] = field(repr=False)
    bcc: tuple[str, ...] = field(repr=False)
    subject: str = field(repr=False)
    text_body: str = field(repr=False)
    secret_reference: str | None = field(repr=False)


EmailPreflightErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "not_found",
    "blocked_recipient",
    "unavailable",
    "timeout",
    "invalid_provider_result",
]


class EmailPreflightError(Exception):
    def __init__(self, code: EmailPreflightErrorCode) -> None:
        self.code: EmailPreflightErrorCode = (
            code
            if type(code) is str and code in get_args(EmailPreflightErrorCode)
            else "unavailable"
        )
        self.retryable = False
        self.delivery_may_have_occurred = False
        super().__init__("Email preflight failed")
