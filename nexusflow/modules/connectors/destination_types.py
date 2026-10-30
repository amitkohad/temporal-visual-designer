"""Internal worker destination planning; never a browser dispatch capability."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, get_args

from pydantic import Field

from nexusflow.contracts.models import Contract, Identifier, Scope


class HTTPDestinationRequest(Contract):
    connection_id: Identifier
    connection_revision: Identifier
    operation_id: Identifier
    path: Annotated[str, Field(min_length=1, max_length=4096, repr=False)]


@dataclass(frozen=True)
class ApprovedHTTPDestination:
    scope: Scope
    connection_id: str
    connection_revision: str
    definition_digest: str
    operation_id: str
    url: str = field(repr=False)
    origin: str
    host: str
    port: int
    ipv4_addresses: tuple[str, ...]
    redirect_count: int = 0


class IPv4Resolver(Protocol):
    async def resolve_ipv4(self, host: str) -> tuple[str, ...]: ...


class WorkerDestinationAuthorization(Protocol):
    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str: ...


DestinationErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "credentials_required",
    "blocked_destination",
    "redirect_limit",
    "unavailable",
]


class DestinationError(Exception):
    def __init__(self, code: DestinationErrorCode) -> None:
        self.code: DestinationErrorCode = (
            code if type(code) is str and code in get_args(DestinationErrorCode) else "unavailable"
        )
        self.retryable = self.code == "unavailable"
        super().__init__("HTTP destination preflight failed")
