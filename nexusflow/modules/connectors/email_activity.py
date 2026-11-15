"""Internal email text activity mapping; raw SMTP results never become outputs."""

import math
from datetime import datetime
from typing import Protocol

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.authorized_email_execution import EmailSendError
from nexusflow.modules.connectors.email_types import EmailTextActivityInput, EmailTextActivityOutput
from nexusflow.modules.project_versioning.canonical import canonical


class EmailActivityLiveExecutor(Protocol):
    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: EmailTextActivityInput | dict[str, object],
        *,
        message_time: datetime,
    ) -> EmailTextActivityOutput: ...


class EmailTextActivityAdapter:
    def __init__(
        self,
        *,
        live: EmailActivityLiveExecutor | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("Invalid email text activity timeout")
        self.live = live
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _inputs(value: EmailTextActivityInput | dict[str, object]) -> EmailTextActivityInput:
        if type(value) is EmailTextActivityInput:
            raw = {
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "execution_mode": value.execution_mode,
                "fixture_digest": value.fixture_digest,
                "to": value.to,
                "cc": value.cc,
                "bcc": value.bcc,
                "subject": value.subject,
                "text_body": value.text_body,
            }
        elif type(value) is dict:
            raw = value
        else:
            raise ValueError
        selected = EmailTextActivityInput.model_validate(raw, strict=True)
        encoded = canonical(
            {
                "connection_id": selected.connection_id,
                "connection_revision": selected.connection_revision,
                "execution_mode": selected.execution_mode,
                "fixture_digest": selected.fixture_digest,
                "to": selected.to,
                "cc": selected.cc,
                "bcc": selected.bcc,
                "subject": selected.subject,
                "text_body": selected.text_body,
            }
        )
        return EmailTextActivityInput.model_validate_json(encoded, strict=True)

    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: EmailTextActivityInput | dict[str, object],
        *,
        message_time: datetime,
    ) -> EmailTextActivityOutput:
        try:
            selected = self._inputs(inputs)
        except Exception as error:
            raise EmailSendError("invalid_request") from error
        if selected.execution_mode != "live" or selected.fixture_digest is not None:
            raise EmailSendError("forbidden") from None
        if self.live is None:
            raise EmailSendError("unavailable") from None
        return await self.live.execute(scope, operation_id, selected, message_time=message_time)
