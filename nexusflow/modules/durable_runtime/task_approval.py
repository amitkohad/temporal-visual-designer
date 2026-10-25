"""Strict host-side configuration for opt-in task-managed approval steps."""

import json
from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from nexusflow.contracts.models import Contract
from nexusflow.modules.human_tasks.forms import prepare_task_form
from nexusflow.modules.human_tasks.types import (
    ApprovalRule,
    ApprovalStage,
    CreateTask,
    PrincipalIds,
)
from nexusflow.modules.project_versioning.types import FormDefinition


class ManagedApprovalForm(Contract):
    definition: FormDefinition
    initial_values: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_form(self) -> "ManagedApprovalForm":
        prepare_task_form(
            json.dumps(self.definition.model_dump(mode="json"), allow_nan=False),
            json.dumps(self.initial_values, allow_nan=False),
        )
        return self


class TaskApproval(Contract):
    form: ManagedApprovalForm | None = None
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
