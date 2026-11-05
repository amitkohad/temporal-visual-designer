"""Bounded text-email authoring contracts; no provider, network or delivery behavior."""

import re
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from nexusflow.contracts.models import Contract, Digest, Identifier

_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
EMAIL_ADDRESS_PATTERN = (
    rf"^(?=[^@]{{1,64}}@){_ATOM}(?:\.{_ATOM})*@(?:{_LABEL}\.)+{_LABEL}$(?![\s\S])"
)
EmailAddress = Annotated[
    str, Field(min_length=3, max_length=254, pattern=re.compile(EMAIL_ADDRESS_PATTERN))
]


def canonical_email_address(value: object) -> str:
    """Normalize the explicit ASCII mailbox subset, without DNS or full syntax claims."""
    try:
        if (
            type(value) is not str
            or not 3 <= len(value) <= 254
            or not value.isascii()
            or re.fullmatch(EMAIL_ADDRESS_PATTERN, value) is None
        ):
            raise ValueError
        local, domain = value.split("@")
        if len(local) > 64:
            raise ValueError
        return local + "@" + domain.lower()
    except Exception:
        raise ValueError("Unsupported email address") from None


class EmailTextActivityInput(Contract):
    connection_id: Identifier
    connection_revision: Identifier
    execution_mode: Literal["live", "mock"]
    fixture_digest: Digest | None = None
    to: Annotated[list[EmailAddress], Field(min_length=1, max_length=50, repr=False)]
    cc: Annotated[list[EmailAddress], Field(max_length=50, repr=False)] = Field(
        default_factory=list
    )
    bcc: Annotated[list[EmailAddress], Field(max_length=50, repr=False)] = Field(
        default_factory=list
    )
    subject: Annotated[str, Field(min_length=1, max_length=256, repr=False)]
    text_body: Annotated[str, Field(min_length=1, max_length=1048576, repr=False)]

    @field_validator("to", "cc", "bcc", mode="before")
    @classmethod
    def recipients(cls, value: object) -> list[str]:
        if type(value) is not list or len(value) > 50:
            raise ValueError("Bounded recipient list required")
        return [canonical_email_address(item) for item in value]

    @field_validator("subject", mode="before")
    @classmethod
    def bounded_subject(cls, value: object) -> str:
        if (
            type(value) is not str
            or not 1 <= len(value) <= 256
            or not value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            or len(value.encode("utf-8")) > 1024
        ):
            raise ValueError("Bounded single-line email subject required")
        return value

    @field_validator("text_body", mode="before")
    @classmethod
    def bounded_text(cls, value: object) -> str:
        if (
            type(value) is not str
            or not 1 <= len(value) <= 1048576
            or "\x00" in value
            or len(value.encode("utf-8")) > 1048576
        ):
            raise ValueError("Bounded UTF-8 email text required")
        return value

    @model_validator(mode="after")
    def supported_combination(self) -> "EmailTextActivityInput":
        if (self.execution_mode == "mock") != (self.fixture_digest is not None):
            raise ValueError("Only mock email authoring requires a fixture digest")
        recipients = self.to + self.cc + self.bcc
        if len(recipients) > 50 or len(set(recipients)) != len(recipients):
            raise ValueError("Recipient total or uniqueness policy is invalid")
        return self


class EmailTextActivityOutput(Contract):
    operation_id: Identifier
    execution_mode: Literal["live", "mock"]
    submission_status: Literal["accepted"]


@dataclass(frozen=True)
class PreparedEmailTextMessage:
    connection_id: str
    connection_revision: str
    execution_mode: Literal["live", "mock"]
    fixture_digest: str | None
    to: tuple[str, ...] = field(repr=False)
    cc: tuple[str, ...] = field(repr=False)
    bcc: tuple[str, ...] = field(repr=False)
    subject: str = field(repr=False)
    text_body: str = field(repr=False)


class EmailAuthoringError(Exception):
    def __init__(self) -> None:
        self.code: Literal["invalid_request"] = "invalid_request"
        super().__init__("Email message validation failed")


def prepare_email_text_message(value: EmailTextActivityInput) -> PreparedEmailTextMessage:
    """Revalidate untrusted model state and freeze a private independent message."""
    try:
        if type(value) is not EmailTextActivityInput:
            raise ValueError
        if any(
            type(item) is not str
            for item in (value.connection_id, value.connection_revision, value.execution_mode)
        ) or (value.fixture_digest is not None and type(value.fixture_digest) is not str):
            raise ValueError
        checked = EmailTextActivityInput.model_validate(
            {
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "execution_mode": value.execution_mode,
                "fixture_digest": value.fixture_digest,
                "to": value.to,
                "cc": value.cc,
                "bcc": value.bcc,
                "subject": value.subject,
                "text_body": value.text_body,
            },
            strict=True,
        )
        return PreparedEmailTextMessage(
            checked.connection_id,
            checked.connection_revision,
            checked.execution_mode,
            checked.fixture_digest,
            tuple(checked.to),
            tuple(checked.cc),
            tuple(checked.bcc),
            checked.subject,
            checked.text_body,
        )
    except Exception:
        raise EmailAuthoringError() from None
