"""Private immutable MIME snapshots; neither delivery nor sending authority."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope


@dataclass(frozen=True)
class PreparedEmailMime:
    envelope: PreparedEmailEnvelope = field(repr=False)
    message_time: datetime
    message_id: str


@dataclass(frozen=True)
class EncodedEmailMessage:
    scope: Scope
    operation_id: str
    connection_id: str
    connection_revision: str
    definition_digest: str
    message_id: str
    message_time: datetime
    content_digest: str
    envelope_sender: str = field(repr=False)
    recipients: tuple[str, ...] = field(repr=False)
    data: bytes = field(repr=False)


EmailEncodingErrorCode = Literal["invalid_request", "message_too_large", "unavailable"]


class EmailEncodingError(Exception):
    def __init__(self, code: EmailEncodingErrorCode) -> None:
        self.code: EmailEncodingErrorCode = (
            code
            if type(code) is str and code in get_args(EmailEncodingErrorCode)
            else "unavailable"
        )
        self.retryable = False
        self.delivery_may_have_occurred = False
        super().__init__("Email encoding failed")
