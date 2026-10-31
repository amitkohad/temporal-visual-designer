"""Trusted worker metadata and prepared mock calls; no live dispatch capability."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.activity_types import (
    HTTPJSONActivityInput,
    HTTPJSONActivityOutput,
)


@dataclass(frozen=True)
class HTTPMockActivityContext:
    namespace: str
    workflow_namespace: str
    task_queue: str
    workflow_type: str
    workflow_id: str
    workflow_run_id: str
    activity_type: str
    activity_id: str
    attempt: int


@dataclass(frozen=True)
class PreparedHTTPMockCall:
    scope: Scope
    operation_id: str
    inputs: HTTPJSONActivityInput = field(repr=False)


class HTTPMockActivityAdapter(Protocol):
    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: HTTPJSONActivityInput | dict[str, object],
    ) -> HTTPJSONActivityOutput: ...


HTTPMockDispatchErrorCode = Literal["invalid_request", "forbidden", "unavailable"]


class HTTPMockDispatchError(Exception):
    def __init__(self, code: HTTPMockDispatchErrorCode) -> None:
        self.code: HTTPMockDispatchErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPMockDispatchErrorCode)
            else "unavailable"
        )
        super().__init__("HTTP mock worker dispatch failed")
