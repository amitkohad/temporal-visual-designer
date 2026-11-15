"""Trusted worker metadata and prepared email text calls for live dispatch."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_types import EmailTextActivityInput, EmailTextActivityOutput


@dataclass(frozen=True)
class EmailTextActivityContext:
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
class PreparedEmailTextCall:
    scope: Scope
    operation_id: str
    inputs: EmailTextActivityInput = field(repr=False)


class EmailTextActivityAdapter(Protocol):
    async def execute(
        self,
        scope: Scope,
        operation_id: str,
        inputs: EmailTextActivityInput | dict[str, object],
        *,
        message_time,
    ) -> EmailTextActivityOutput: ...


EmailTextDispatchErrorCode = Literal["invalid_request", "forbidden", "unavailable"]


class EmailTextDispatchError(Exception):
    def __init__(self, code: EmailTextDispatchErrorCode) -> None:
        self.code: EmailTextDispatchErrorCode = (
            code
            if type(code) is str and code in get_args(EmailTextDispatchErrorCode)
            else "unavailable"
        )
        super().__init__("Email text worker dispatch failed")
