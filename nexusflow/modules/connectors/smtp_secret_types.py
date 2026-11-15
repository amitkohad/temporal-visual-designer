"""Opaque SMTP login secret resolution; values never enter public metadata."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.secret_types import SecretResolverError

SMTPAuthScheme = Literal["login"]


@dataclass(frozen=True)
class SMTPLoginCredential:
    """Private worker SMTP AUTH handle; never log or export username/password."""

    scheme: SMTPAuthScheme
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.scheme not in get_args(SMTPAuthScheme):
            raise ValueError("Unsupported SMTP authorization scheme")
        for label, value, limit in (
            ("username", self.username, 256),
            ("password", self.password, 8192),
        ):
            if (
                type(value) is not str
                or not 1 <= len(value) <= limit
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or any(char in value for char in ("\r", "\n"))
            ):
                raise ValueError(f"Invalid SMTP {label}")

    def __repr__(self) -> str:
        return f"SMTPLoginCredential(scheme={self.scheme!r})"


class EmailSecretResolver(Protocol):
    def resolve(self, scope: Scope, secret_reference: Identifier) -> SMTPLoginCredential: ...


# Re-export for call sites that import SMTP errors from this module.
__all__ = [
    "SMTPAuthScheme",
    "SMTPLoginCredential",
    "EmailSecretResolver",
    "SecretResolverError",
]
