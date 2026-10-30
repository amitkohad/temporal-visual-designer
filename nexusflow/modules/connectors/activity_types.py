"""HTTP JSON activity contracts; business input never selects scope or operation ID."""

import math
from typing import Annotated, Literal, Protocol, get_args

from pydantic import Field, JsonValue, field_validator, model_validator

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.policy import request_url
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPAttemptResult
from nexusflow.modules.project_versioning.canonical import canonical


class HTTPJSONActivityInput(Contract):
    connection_id: Identifier
    connection_revision: Identifier
    execution_mode: Literal["live", "mock"]
    fixture_digest: Digest | None = None
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] = "GET"
    path: Annotated[str, Field(min_length=1, max_length=4096, repr=False)]
    json_body: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @field_validator("json_body", mode="before")
    @classmethod
    def bounded_body(cls, value: object) -> object:
        if value is None:
            return value
        if type(value) is not dict:
            raise ValueError("JSON object body required")
        pending: list[tuple[object, int]] = [(value, 0)]
        visited: set[int] = set()
        count = 0
        characters = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > 10000 or depth > 32:
                raise ValueError("JSON body exceeds structural limits")
            if isinstance(item, (dict, list)) and type(item) in (dict, list):
                if len(item) + len(pending) + count > 10000:
                    raise ValueError("JSON body exceeds structural limits")
                if id(item) in visited:
                    raise ValueError("JSON body must be a tree")
                visited.add(id(item))
                if type(item) is dict:
                    if any(type(key) is not str for key in item):
                        raise ValueError("JSON object keys must be strings")
                    characters += sum(len(key) for key in item)
                    pending.extend((child, depth + 1) for child in item.values())
                else:
                    pending.extend((child, depth + 1) for child in item)
            elif item is not None and type(item) not in (str, int, float, bool):
                raise ValueError("JSON body contains unsupported values")
            elif type(item) is float and not math.isfinite(item):
                raise ValueError("JSON body contains unsupported values")
            elif type(item) is str:
                characters += len(item)
            if characters > 1048576:
                raise ValueError("JSON body exceeds byte limit")
        if len(canonical(value).encode("utf-8")) > 1048576:
            raise ValueError("JSON body exceeds byte limit")
        return value

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        try:
            request_url("https://activity.example.com", value)
        except Exception:
            raise ValueError("Origin-relative HTTP path required") from None
        return value

    @model_validator(mode="after")
    def supported_combination(self) -> "HTTPJSONActivityInput":
        if (self.execution_mode == "mock") != (self.fixture_digest is not None):
            raise ValueError("Only mock execution requires a fixture digest")
        if self.method in {"GET", "HEAD"} and self.json_body is not None:
            raise ValueError("GET and HEAD do not support a JSON body")
        return self


class HTTPJSONActivityOutput(Contract):
    operation_id: Identifier
    status_code: Annotated[int, Field(ge=200, le=599)]
    execution_mode: Literal["live", "mock"]


class HTTPActivityLiveExecutor(Protocol):
    async def execute(
        self, scope: Scope, destination: HTTPDestinationRequest, request: HTTPAttemptRequest
    ) -> HTTPAttemptResult: ...


class HTTPActivityMockExecutor(Protocol):
    async def execute(
        self,
        scope: Scope,
        destination: HTTPDestinationRequest,
        request: HTTPAttemptRequest,
        *,
        fixture_digest: str,
    ) -> HTTPAttemptResult: ...


HTTPActivityErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "credentials_required",
    "blocked_destination",
    "not_found",
    "fixture_mismatch",
    "unavailable",
    "timeout",
    "protocol_error",
    "response_too_large",
    "invalid_provider_result",
]


class HTTPActivityError(Exception):
    def __init__(
        self, code: HTTPActivityErrorCode, *, delivery_may_have_occurred: bool = False
    ) -> None:
        self.code: HTTPActivityErrorCode = (
            code if type(code) is str and code in get_args(HTTPActivityErrorCode) else "unavailable"
        )
        self.delivery_may_have_occurred = (
            delivery_may_have_occurred if type(delivery_may_have_occurred) is bool else True
        )
        self.retryable = False
        super().__init__("HTTP JSON activity failed")
