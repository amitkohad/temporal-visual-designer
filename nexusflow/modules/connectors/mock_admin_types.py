"""JSON-only fixture administration; identities come from the authorized route."""

from typing import Annotated, Literal, Protocol, get_args

from pydantic import Field, JsonValue, field_validator, model_validator

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.connectors.activity_types import HTTPJSONActivityInput
from nexusflow.modules.connectors.mock_storage_types import HTTPMockFixtureReceipt
from nexusflow.modules.connectors.mock_types import HTTPMockScenario


class HTTPMockFixtureWrite(Contract):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] = "GET"
    path: Annotated[str, Field(min_length=1, max_length=4096, repr=False)]
    json_body: dict[str, JsonValue] | None = Field(default=None, repr=False)
    status_code: Annotated[int, Field(strict=True, ge=200, le=599)]
    response_json_body: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @field_validator("json_body", "response_json_body", mode="before")
    @classmethod
    def bounded_json(cls, value: object) -> object:
        return HTTPJSONActivityInput.bounded_body(value)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return HTTPJSONActivityInput.relative_path(value)

    @model_validator(mode="after")
    def supported_combination(self) -> "HTTPMockFixtureWrite":
        if self.method in {"GET", "HEAD"} and self.json_body is not None:
            raise ValueError("GET and HEAD do not support a JSON body")
        if (self.method == "HEAD" or self.status_code in {204, 304}) and (
            self.response_json_body is not None
        ):
            raise ValueError("This response does not support a body")
        return self


class HTTPMockFixtureMetadata(Contract):
    scope: Scope
    connection_id: Identifier
    connection_revision: Identifier
    fixture_digest: Digest
    stored_bytes: Annotated[int, Field(strict=True, ge=1, le=12582912)]


class HTTPMockFixtureStore(Protocol):
    def put(self, value: HTTPMockScenario) -> HTTPMockFixtureReceipt: ...

    async def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> HTTPMockScenario | None: ...


HTTPMockFixtureAdminErrorCode = Literal[
    "invalid_request", "forbidden", "not_found", "conflict", "limit_exceeded", "unavailable"
]


class HTTPMockFixtureAdminError(Exception):
    def __init__(self, code: HTTPMockFixtureAdminErrorCode) -> None:
        self.code: HTTPMockFixtureAdminErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPMockFixtureAdminErrorCode)
            else "unavailable"
        )
        super().__init__("HTTP mock fixture administration failed")
