"""Private SMTP TLS definitions and metadata-only email administration ports."""

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BeforeValidator, Field, field_validator

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.connectors.email_types import canonical_email_address


def canonical_email_domain(value: object) -> str:
    """Bounded qualified ASCII names; no DNS, routing, or egress authorization."""
    try:
        if (
            type(value) is not str
            or not 1 <= len(value) <= 253
            or not value.isascii()
            or "." not in value
            or value.endswith(".")
        ):
            raise ValueError
        selected = value.lower()
        if not any(char.isalpha() for char in selected) or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in selected.split(".")
        ):
            raise ValueError
        try:
            ipaddress.ip_address(selected)
        except ValueError:
            return selected
        raise ValueError
    except Exception:
        raise ValueError("Invalid email DNS name") from None


def _domains(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, (tuple, list))
        or type(value) not in (tuple, list)
        or not 1 <= len(value) <= 50
    ):
        raise ValueError("Bounded recipient domain list required")
    selected = tuple(canonical_email_domain(item) for item in value)
    if len(set(selected)) != len(selected):
        raise ValueError("Duplicate recipient domain")
    return tuple(sorted(selected))


class EmailConnectionWrite(Contract):
    connector_type: Literal["email"] = "email"
    connector_version: Literal["1.0.0"] = "1.0.0"
    transport: Literal["smtp_tls"] = "smtp_tls"
    smtp_host: Annotated[str, Field(min_length=1, max_length=253, repr=False)]
    port: Annotated[int, Field(strict=True, ge=1, le=65535, repr=False)] = 465
    sender: Annotated[str, Field(min_length=3, max_length=254, repr=False)]
    allowed_recipient_domains: Annotated[
        tuple[str, ...], BeforeValidator(_domains), Field(min_length=1, max_length=50, repr=False)
    ]
    secret_reference: Identifier | None = Field(default=None, repr=False)

    @field_validator("smtp_host", mode="before")
    @classmethod
    def host(cls, value: object) -> str:
        return canonical_email_domain(value)

    @field_validator("sender", mode="before")
    @classmethod
    def mailbox(cls, value: object) -> str:
        return canonical_email_address(value)


class EmailConnectionMetadata(Contract):
    scope: Scope
    connection_id: Identifier
    connection_revision: Identifier
    revision: Annotated[int, Field(strict=True, ge=1, lt=2**63)]
    connector_type: Literal["email"] = "email"
    connector_version: Literal["1.0.0"] = "1.0.0"
    transport: Literal["smtp_tls"] = "smtp_tls"
    definition_digest: Digest
    has_secret_reference: bool
    actor_id: Identifier
    created_at: Annotated[str, Field(min_length=1, max_length=64)]


class EmailConnectionPage(Contract):
    items: Annotated[tuple[EmailConnectionMetadata, ...], Field(max_length=50)]
    next_before_revision: Annotated[int, Field(strict=True, ge=1, lt=2**63)] | None


@dataclass(frozen=True)
class EmailConnectionRecord:
    metadata: EmailConnectionMetadata
    definition_json: str = field(repr=False)


@runtime_checkable
class EmailConnectionRepository(Protocol):
    def put(
        self,
        scope: Scope,
        connection_id: str,
        connection_revision: str,
        request: EmailConnectionWrite,
        actor_id: str,
    ) -> EmailConnectionMetadata: ...

    def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> EmailConnectionRecord: ...

    def metadata(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> EmailConnectionMetadata: ...

    def list(
        self, scope: Scope, connection_id: str, limit: int = 25, before_revision: int | None = None
    ) -> EmailConnectionPage: ...
