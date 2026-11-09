"""Pure pinned email configuration and exact-recipient policy; never delivery."""

import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionRecord,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.email_preflight_types import (
    EmailPreflightError,
    PreparedEmailEnvelope,
)
from nexusflow.modules.connectors.email_types import (
    EmailTextActivityInput,
    prepare_email_text_message,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest


def _identifier(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Identifier).validate_python(value, strict=True)


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope(
        organization_id=_identifier(value.organization_id),
        environment_id=_identifier(value.environment_id),
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError


def _definition(
    record: EmailConnectionRecord,
) -> tuple[EmailConnectionMetadata, EmailConnectionWrite]:
    if (
        type(record) is not EmailConnectionRecord
        or type(record.metadata) is not EmailConnectionMetadata
    ):
        raise ValueError
    value = record.metadata
    scope = _scope(value.scope)
    for item in (
        value.connection_id,
        value.connection_revision,
        value.connector_type,
        value.connector_version,
        value.transport,
        value.definition_digest,
        value.actor_id,
        value.created_at,
    ):
        if type(item) is not str:
            raise ValueError
    if type(value.revision) is not int or type(value.has_secret_reference) is not bool:
        raise ValueError
    metadata = EmailConnectionMetadata.model_validate(
        {
            "scope": scope,
            "connection_id": value.connection_id,
            "connection_revision": value.connection_revision,
            "revision": value.revision,
            "connector_type": value.connector_type,
            "connector_version": value.connector_version,
            "transport": value.transport,
            "definition_digest": value.definition_digest,
            "has_secret_reference": value.has_secret_reference,
            "actor_id": value.actor_id,
            "created_at": value.created_at,
        },
        strict=True,
    )
    timestamp = datetime.fromisoformat(metadata.created_at)
    if timestamp.utcoffset() != timedelta(0) or timestamp.isoformat() != metadata.created_at:
        raise ValueError
    encoded = record.definition_json
    if type(encoded) is not str or len(encoded) > 16384 or len(encoded.encode("utf-8")) > 16384:
        raise ValueError
    document = json.loads(encoded, object_pairs_hook=_unique, parse_constant=_reject_constant)
    if type(document) is not dict:
        raise ValueError
    definition = EmailConnectionWrite.model_validate(document, strict=True)
    snapshot = definition.model_dump(mode="json")
    if (
        canonical(snapshot) != encoded
        or digest(snapshot) != metadata.definition_digest
        or metadata.has_secret_reference != (definition.secret_reference is not None)
    ):
        raise ValueError
    return metadata, definition


def prepare_email_envelope(
    scope: Scope,
    operation_id: str,
    record: EmailConnectionRecord,
    inputs: EmailTextActivityInput,
) -> PreparedEmailEnvelope:
    """Snapshot exact authorized configuration; this performs no authorization or I/O."""
    try:
        selected = _scope(scope)
        selected_operation = _identifier(operation_id)
        message = prepare_email_text_message(inputs)
    except Exception:
        raise EmailPreflightError("invalid_request") from None
    if message.execution_mode != "live":
        raise EmailPreflightError("forbidden") from None
    try:
        metadata, definition = _definition(record)
        if (
            metadata.scope != selected
            or metadata.connection_id != message.connection_id
            or metadata.connection_revision != message.connection_revision
        ):
            raise ValueError
    except Exception:
        raise EmailPreflightError("invalid_provider_result") from None
    allowed = frozenset(definition.allowed_recipient_domains)
    if any(
        address.rsplit("@", 1)[1] not in allowed
        for address in (*message.to, *message.cc, *message.bcc)
    ):
        raise EmailPreflightError("blocked_recipient") from None
    return PreparedEmailEnvelope(
        scope=selected,
        operation_id=selected_operation,
        connection_id=metadata.connection_id,
        connection_revision=metadata.connection_revision,
        definition_digest=metadata.definition_digest,
        execution_mode="live",
        smtp_host=definition.smtp_host,
        port=definition.port,
        sender=definition.sender,
        to=message.to,
        cc=message.cc,
        bcc=message.bcc,
        subject=message.subject,
        text_body=message.text_body,
        secret_reference=definition.secret_reference,
    )
