"""Pure MIME boundary validation and deterministic logical message identity."""

import re
from datetime import UTC, datetime

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_connection_types import canonical_email_domain
from nexusflow.modules.connectors.email_mime_types import EmailEncodingError, PreparedEmailMime
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope
from nexusflow.modules.connectors.email_types import (
    EmailTextActivityInput,
    canonical_email_address,
    prepare_email_text_message,
)
from nexusflow.modules.project_versioning.canonical import digest


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None
    ):
        raise ValueError
    return value


def prepare_email_mime(
    envelope: PreparedEmailEnvelope, message_time: datetime
) -> PreparedEmailMime:
    """Snapshot canonical inputs; does not authorize or bind payloads to an operation."""
    try:
        if type(envelope) is not PreparedEmailEnvelope or type(envelope.scope) is not Scope:
            raise ValueError
        scope = Scope(
            organization_id=_identifier(envelope.scope.organization_id),
            environment_id=_identifier(envelope.scope.environment_id),
        )
        operation_id = _identifier(envelope.operation_id)
        connection_id = _identifier(envelope.connection_id)
        connection_revision = _identifier(envelope.connection_revision)
        if (
            type(envelope.definition_digest) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", envelope.definition_digest) is None
            or type(envelope.execution_mode) is not str
            or envelope.execution_mode != "live"
            or canonical_email_domain(envelope.smtp_host) != envelope.smtp_host
            or type(envelope.port) is not int
            or not 1 <= envelope.port <= 65535
            or canonical_email_address(envelope.sender) != envelope.sender
        ):
            raise ValueError
        if envelope.secret_reference is not None:
            _identifier(envelope.secret_reference)
        for recipients in (envelope.to, envelope.cc, envelope.bcc):
            if type(recipients) is not tuple or len(recipients) > 50:
                raise ValueError
            if any(canonical_email_address(value) != value for value in recipients):
                raise ValueError
        message = prepare_email_text_message(
            EmailTextActivityInput(
                connection_id=connection_id,
                connection_revision=connection_revision,
                execution_mode="live",
                to=list(envelope.to),
                cc=list(envelope.cc),
                bcc=list(envelope.bcc),
                subject=envelope.subject,
                text_body=envelope.text_body,
            )
        )
        if (
            type(message_time) is not datetime
            or message_time.tzinfo is not UTC
            or message_time.microsecond != 0
            or message_time.year < 1900
        ):
            raise ValueError
        snapshot = PreparedEmailEnvelope(
            scope=scope,
            operation_id=operation_id,
            connection_id=connection_id,
            connection_revision=connection_revision,
            definition_digest=envelope.definition_digest,
            execution_mode="live",
            smtp_host=envelope.smtp_host,
            port=envelope.port,
            sender=envelope.sender,
            to=message.to,
            cc=message.cc,
            bcc=message.bcc,
            subject=message.subject,
            text_body=message.text_body,
            secret_reference=envelope.secret_reference,
        )
        identity = {
            "kind": "email-message-id",
            "schema_version": "0.1",
            "scope": scope.model_dump(mode="json"),
            "operation_id": operation_id,
            "connection_id": connection_id,
            "connection_revision": connection_revision,
            "definition_digest": envelope.definition_digest,
        }
        message_id = (
            "<nexusflow-" + digest(identity).removeprefix("sha256:") + "@nexusflow.invalid>"
        )
        return PreparedEmailMime(snapshot, message_time, message_id)
    except Exception:
        raise EmailEncodingError("invalid_request") from None
