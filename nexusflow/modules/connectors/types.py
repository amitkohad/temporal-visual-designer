"""Immutable HTTP connection metadata and private definition ports."""

import ipaddress
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import AfterValidator, BeforeValidator, Field, field_validator, model_validator

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope


def _https_origin(value: str) -> str:
    """Canonical ASCII DNS origin only; this performs no DNS or egress check."""
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value) or "\\" in value:
        raise ValueError("Invalid HTTPS origin")
    parsed = urlsplit(value)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or host.endswith(".")
        or "." not in host
        or len(host) > 253
    ):
        raise ValueError("Invalid HTTPS origin")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("DNS origin required")
    if not any(char.isalpha() for char in host) or any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
        for label in host.split(".")
    ):
        raise ValueError("Invalid DNS origin")
    port = parsed.port
    if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ValueError("Invalid origin port")
    return "https://" + host + (f":{port}" if port not in (None, 443) else "")


def https_origin(value: str) -> str:
    try:
        return _https_origin(value)
    except ValueError:
        raise ValueError("Invalid HTTPS origin") from None


HTTPSOrigin = Annotated[str, Field(min_length=8, max_length=2048), AfterValidator(https_origin)]


class HTTPConnectionWrite(Contract):
    connector_type: Literal["http"] = "http"
    connector_version: Literal["1.0.0"] = "1.0.0"
    base_origin: HTTPSOrigin
    allowed_origins: Annotated[
        tuple[HTTPSOrigin, ...],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
        Field(min_length=1, max_length=50),
    ]
    secret_reference: Identifier | None = None

    @field_validator("allowed_origins")
    @classmethod
    def canonical_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate origin")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def allowed_base(self) -> "HTTPConnectionWrite":
        if self.base_origin not in self.allowed_origins:
            raise ValueError("Base origin must be allowed")
        return self


class ConnectionMetadata(Contract):
    scope: Scope
    connection_id: Identifier
    connection_revision: Identifier
    revision: Annotated[int, Field(ge=1, lt=2**63)]
    connector_type: Literal["http"] = "http"
    connector_version: Literal["1.0.0"] = "1.0.0"
    definition_digest: Digest
    has_secret_reference: bool
    actor_id: Identifier
    created_at: Annotated[str, Field(min_length=1, max_length=64)]


class ConnectionPage(Contract):
    items: Annotated[tuple[ConnectionMetadata, ...], Field(max_length=50)]
    next_before_revision: int | None


@dataclass(frozen=True)
class ConnectionRecord:
    metadata: ConnectionMetadata
    definition_json: str


class ConnectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@runtime_checkable
class ConnectionRepository(Protocol):
    def put(
        self,
        scope: Scope,
        connection_id: str,
        connection_revision: str,
        request: HTTPConnectionWrite,
        actor_id: str,
    ) -> ConnectionMetadata: ...
    def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> ConnectionRecord: ...
    def metadata(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> ConnectionMetadata: ...
    def list(
        self, scope: Scope, connection_id: str, limit: int = 25, before_revision: int | None = None
    ) -> ConnectionPage: ...
