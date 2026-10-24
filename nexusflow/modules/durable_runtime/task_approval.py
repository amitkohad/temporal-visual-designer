"""Strict host-side configuration for opt-in task-managed approval steps."""

from typing import Annotated

from pydantic import Field, model_validator

from nexusflow.contracts.models import Contract
from nexusflow.modules.human_tasks.types import (
    ApprovalRule,
    ApprovalStage,
    CreateTask,
    PrincipalIds,
)


class TaskApproval(Contract):
    rule: ApprovalRule
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()
    stages: Annotated[tuple[ApprovalStage, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def valid_assignment(self) -> "TaskApproval":
        CreateTask(
            run_id="validation",
            node_invocation_id="validation",
            definition_revision="validation",
            rule=self.rule,
            user_ids=self.user_ids,
            group_ids=self.group_ids,
            stages=self.stages,
        )
        return self


def parse_task_approval(value: str) -> TaskApproval:
    return TaskApproval.model_validate_json(value)
