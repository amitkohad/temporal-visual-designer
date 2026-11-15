"""Secret-aware live email send; preflight and MIME remain offline-only."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal, get_args

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.email_mime import encode_email_message
from nexusflow.modules.connectors.email_mime_types import EmailEncodingError
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner
from nexusflow.modules.connectors.email_preflight_types import EmailPreflightError
from nexusflow.modules.connectors.email_types import (
    EmailTextActivityInput,
    EmailTextActivityOutput,
)
from nexusflow.modules.connectors.secret_types import SecretResolverError
from nexusflow.modules.connectors.smtp_secret_types import EmailSecretResolver, SMTPLoginCredential
from nexusflow.modules.connectors.smtp_transport_types import (
    PinnedSMTPSender,
    SMTPSendRequest,
    SMTPTransportError,
)

EmailSendErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "not_found",
    "blocked_recipient",
    "credentials_required",
    "message_too_large",
    "unavailable",
    "timeout",
    "delivery_uncertain",
    "invalid_provider_result",
]


class EmailSendError(Exception):
    def __init__(
        self,
        code: EmailSendErrorCode,
        *,
        delivery_may_have_occurred: bool = False,
    ) -> None:
        self.code: EmailSendErrorCode = (
            code if type(code) is str and code in get_args(EmailSendErrorCode) else "unavailable"
        )
        self.retryable = False
        self.delivery_may_have_occurred = bool(delivery_may_have_occurred)
        super().__init__("Email send failed")


class AuthorizedEmailSendExecutor:
    """Compose preflight → MIME → secret resolve → SMTPS; secrets never become outputs."""

    def __init__(
        self,
        planner: EmailPreflightPlanner,
        secrets: EmailSecretResolver,
        sender: PinnedSMTPSender,
        *,
        timeout_seconds: float = 30,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 120
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("Invalid email send timeout")
        self.planner = planner
        self.secrets = secrets
        self.sender = sender
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _scope(value: Scope) -> Scope:
        if type(value) is not Scope:
            raise ValueError
        return Scope.model_validate(
            {"organization_id": value.organization_id, "environment_id": value.environment_id},
            strict=True,
        )

    def _resolve(self, scope: Scope, reference: str) -> SMTPLoginCredential:
        try:
            selected = TypeAdapter(Identifier).validate_python(reference, strict=True)
            return self.secrets.resolve(scope, selected)
        except SecretResolverError:
            raise
        except Exception as error:
            raise SecretResolverError("unavailable") from error

    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: EmailTextActivityInput | dict[str, object],
        *,
        message_time: datetime,
    ) -> EmailTextActivityOutput:
        try:
            selected = self._scope(scope)
            if type(operation_id) is not str or not 1 <= len(operation_id) <= 255:
                raise EmailSendError("invalid_request") from None
            if type(message_time) is not datetime or message_time.tzinfo is None:
                raise EmailSendError("invalid_request") from None
            if type(inputs) is dict:
                payload = EmailTextActivityInput.model_validate(inputs, strict=True)
            elif type(inputs) is EmailTextActivityInput:
                payload = EmailTextActivityInput.model_validate(
                    {
                        "connection_id": inputs.connection_id,
                        "connection_revision": inputs.connection_revision,
                        "execution_mode": inputs.execution_mode,
                        "fixture_digest": inputs.fixture_digest,
                        "to": inputs.to,
                        "cc": inputs.cc,
                        "bcc": inputs.bcc,
                        "subject": inputs.subject,
                        "text_body": inputs.text_body,
                    },
                    strict=True,
                )
            else:
                raise EmailSendError("invalid_request") from None
            if payload.execution_mode != "live" or payload.fixture_digest is not None:
                raise EmailSendError("forbidden") from None
        except EmailSendError:
            raise
        except Exception:
            raise EmailSendError("invalid_request") from None

        try:
            envelope = await self.planner.prepare(selected, operation_id, payload)
        except EmailPreflightError as error:
            code = error.code if error.code in get_args(EmailSendErrorCode) else "unavailable"
            raise EmailSendError(code) from error  # type: ignore[arg-type]
        except Exception as error:
            raise EmailSendError("unavailable") from error

        if envelope.secret_reference is None:
            raise EmailSendError("credentials_required") from None

        try:
            credential = self._resolve(selected, envelope.secret_reference)
        except SecretResolverError as error:
            mapped = (
                error.code
                if error.code in {"credentials_required", "forbidden", "invalid_request"}
                else "unavailable"
            )
            raise EmailSendError(mapped) from error  # type: ignore[arg-type]

        try:
            encoded = encode_email_message(envelope, message_time=message_time)
        except EmailEncodingError as error:
            mapped = (
                error.code
                if error.code in {"invalid_request", "message_too_large", "unavailable"}
                else "unavailable"
            )
            raise EmailSendError(mapped) from error  # type: ignore[arg-type]

        def before_write() -> None:
            try:
                self.planner.authorization.authorize(
                    selected, "worker.execute", envelope.connection_id
                )
            except Exception as error:
                raise EmailSendError("forbidden") from error
            refreshed = self._resolve(selected, envelope.secret_reference)  # type: ignore[arg-type]
            if refreshed.scheme != credential.scheme:
                raise EmailSendError("forbidden") from None

        try:
            result = self.sender.send(
                operation_id,
                SMTPSendRequest(
                    smtp_host=envelope.smtp_host,
                    port=envelope.port,
                    envelope_sender=encoded.envelope_sender,
                    recipients=encoded.recipients,
                    data=encoded.data,
                    credential=credential,
                ),
                before_write=before_write,
                timeout_seconds=self.timeout_seconds,
            )
        except EmailSendError:
            raise
        except SecretResolverError as error:
            mapped = (
                error.code
                if error.code in {"credentials_required", "forbidden", "invalid_request"}
                else "unavailable"
            )
            raise EmailSendError(mapped) from error  # type: ignore[arg-type]
        except SMTPTransportError as error:
            mapped = error.code if error.code in get_args(EmailSendErrorCode) else "unavailable"
            raise EmailSendError(
                mapped,  # type: ignore[arg-type]
                delivery_may_have_occurred=error.delivery_may_have_occurred,
            ) from error
        except Exception as error:
            raise EmailSendError("unavailable") from error

        try:
            if (
                type(result.operation_id) is not str
                or result.operation_id != operation_id
                or result.submission_status != "accepted"
            ):
                raise EmailSendError("invalid_provider_result") from None
            return EmailTextActivityOutput(
                operation_id=operation_id,
                execution_mode="live",
                submission_status="accepted",
            )
        except EmailSendError:
            raise
        except Exception as error:
            raise EmailSendError("invalid_provider_result") from error
