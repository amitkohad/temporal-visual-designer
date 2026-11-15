"""Trusted worker metadata and prepared HTTP JSON calls for live or mock dispatch."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.activity_types import (
    HTTPJSONActivityInput,
    HTTPJSONActivityOutput,
)


@dataclass(frozen=True)
class HTTPJSONActivityContext:
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
class PreparedHTTPJSONCall:
    scope: Scope
    operation_id: str
    inputs: HTTPJSONActivityInput = field(repr=False)


class HTTPJSONActivityAdapter(Protocol):
    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: HTTPJSONActivityInput | dict[str, object],
    ) -> HTTPJSONActivityOutput: ...


HTTPJSONDispatchErrorCode = Literal["invalid_request", "forbidden", "unavailable"]


class HTTPJSONDispatchError(Exception):
    def __init__(self, code: HTTPJSONDispatchErrorCode) -> None:
        self.code: HTTPJSONDispatchErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPJSONDispatchErrorCode)
            else "unavailable"
        )
        super().__init__("HTTP JSON worker dispatch failed")
