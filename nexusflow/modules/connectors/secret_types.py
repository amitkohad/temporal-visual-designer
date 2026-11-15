"""Opaque HTTP secret resolution contracts; values never enter public metadata."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Identifier, Scope

HTTPAuthorizationScheme = Literal["Bearer"]

SecretResolverErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "credentials_required",
    "unavailable",
]


class SecretResolverError(Exception):
    def __init__(self, code: SecretResolverErrorCode) -> None:
        self.code: SecretResolverErrorCode = (
            code
            if type(code) is str and code in get_args(SecretResolverErrorCode)
            else "unavailable"
        )
        self.retryable = False
        super().__init__("HTTP secret resolution failed")


@dataclass(frozen=True)
class HTTPAuthorizationCredential:
    """Private worker credential handle; never log or export token material."""

    scheme: HTTPAuthorizationScheme
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.scheme not in get_args(HTTPAuthorizationScheme):
            raise ValueError("Unsupported HTTP authorization scheme")
        if (
            type(self.token) is not str
            or not 1 <= len(self.token) <= 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in self.token)
            or any(char in self.token for char in ("\r", "\n"))
        ):
            raise ValueError("Invalid HTTP authorization token")

    def authorization_header(self) -> str:
        return f"{self.scheme} {self.token}"

    def __repr__(self) -> str:
        return f"HTTPAuthorizationCredential(scheme={self.scheme!r})"


class SecretResolver(Protocol):
    def resolve(
        self, scope: Scope, secret_reference: Identifier
    ) -> HTTPAuthorizationCredential: ...
