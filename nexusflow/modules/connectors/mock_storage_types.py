"""Trusted-host persistence contracts; never a business-user fixture API."""

from dataclasses import dataclass
from typing import Literal, get_args

from nexusflow.contracts.models import Scope


@dataclass(frozen=True)
class HTTPMockFixtureReceipt:
    scope: Scope
    connection_id: str
    connection_revision: str
    fixture_digest: str
    stored_bytes: int


HTTPMockStorageErrorCode = Literal["invalid_request", "conflict", "limit_exceeded", "unavailable"]


class HTTPMockStorageError(Exception):
    def __init__(self, code: HTTPMockStorageErrorCode) -> None:
        self.code: HTTPMockStorageErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPMockStorageErrorCode)
            else "unavailable"
        )
        super().__init__("HTTP mock fixture storage failed")
