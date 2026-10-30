"""Explicit Dev-only mock ports; fixtures and raw results are private host data."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest


@dataclass(frozen=True)
class HTTPMockGrant:
    scope: Scope
    actor_id: str
    environment_kind: Literal["dev", "uat", "prod"]


@dataclass(frozen=True)
class HTTPMockScenario:
    scope: Scope
    connection_id: str
    connection_revision: str
    expected_path: str = field(repr=False)
    expected_request: HTTPAttemptRequest = field(repr=False)
    status_code: int
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)


class HTTPMockPolicy(Protocol):
    def authorize(self, scope: Scope, permission: str, connection_id: str) -> HTTPMockGrant: ...


class HTTPMockScenarios(Protocol):
    async def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> HTTPMockScenario | None: ...


HTTPMockErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "not_found",
    "fixture_mismatch",
    "unavailable",
    "timeout",
    "invalid_provider_result",
]


class HTTPMockError(Exception):
    def __init__(self, code: HTTPMockErrorCode) -> None:
        self.code: HTTPMockErrorCode = (
            code if type(code) is str and code in get_args(HTTPMockErrorCode) else "unavailable"
        )
        self.retryable = False
        self.delivery_may_have_occurred = False
        super().__init__("HTTP mock execution failed")
