"""Deterministic private email bytes; serialization never opens a transport."""

import base64
import hashlib
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime

from nexusflow.modules.connectors.email_mime_policy import prepare_email_mime
from nexusflow.modules.connectors.email_mime_types import EmailEncodingError, EncodedEmailMessage
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope

_POLICY = SMTP.clone(
    utf8=False, max_line_length=78, cte_type="7bit", linesep="\r\n", refold_source="none"
)


def _address(value: str) -> Address:
    local, domain = value.rsplit("@", 1)
    return Address(username=local, domain=domain)


def _subject(value: str) -> str:
    # Every word independently decodes as UTF-8; <=39 bytes gives <=64 ASCII
    # characters per word and <=73 columns including the first Subject prefix,
    # within RFC 2047's 76-column limit for lines containing encoded words.
    # Encoding all characters preserves spaces and Unicode line separators;
    # neither headerregistry refolding nor Header.splitlines may rewrite them.
    chunks: list[bytes] = []
    current = b""
    for character in value:
        encoded = character.encode("utf-8")
        if len(current) + len(encoded) > 39:
            chunks.append(current)
            current = b""
        current += encoded
    if current:
        chunks.append(current)
    return "\r\n ".join(
        "=?utf-8?b?" + base64.b64encode(chunk).decode("ascii") + "?=" for chunk in chunks
    )


def encode_email_message(
    envelope: PreparedEmailEnvelope,
    *,
    message_time: datetime,
    max_message_bytes: int = 2_097_152,
) -> EncodedEmailMessage:
    """Encode a validated snapshot, without authorizing or attempting delivery."""
    if type(max_message_bytes) is not int or not 1 <= max_message_bytes <= 8_388_608:
        raise EmailEncodingError("invalid_request") from None
    prepared = prepare_email_mime(envelope, message_time)
    value = prepared.envelope
    try:
        message = EmailMessage(policy=_POLICY)
        message["From"] = _address(value.sender)
        message["To"] = tuple(_address(item) for item in value.to)
        if value.cc:
            message["Cc"] = tuple(_address(item) for item in value.cc)
        # Only generated ASCII RFC 2047 words enter set_raw, never caller headers.
        message.set_raw("Subject", _subject(value.subject))
        message["Date"] = format_datetime(prepared.message_time, usegmt=True)
        message["Message-ID"] = prepared.message_id
        body = value.text_body.replace("\r\n", "\n").replace("\r", "\n")
        if not body.endswith("\n"):
            body += "\n"
        message.set_content(body, subtype="plain", charset="utf-8", cte="base64")
        data = message.as_bytes(policy=_POLICY)
        if (
            type(data) is not bytes
            or not data.isascii()
            or not data.endswith(b"\r\n")
            or b"\r" in data.replace(b"\r\n", b"")
            or b"\n" in data.replace(b"\r\n", b"")
            or any(len(line) > 998 for line in data.split(b"\r\n"))
        ):
            raise ValueError
    except Exception:
        raise EmailEncodingError("unavailable") from None
    if len(data) > max_message_bytes:
        raise EmailEncodingError("message_too_large") from None
    return EncodedEmailMessage(
        scope=value.scope,
        operation_id=value.operation_id,
        connection_id=value.connection_id,
        connection_revision=value.connection_revision,
        definition_digest=value.definition_digest,
        message_id=prepared.message_id,
        message_time=prepared.message_time,
        content_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        envelope_sender=value.sender,
        recipients=value.to + value.cc + value.bcc,
        data=data,
    )
