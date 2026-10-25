"""Trusted worker-side creation of approval tasks and pinned completion routes."""

import json

from pydantic import TypeAdapter
from temporalio import activity
from temporalio.client import Client

from nexusflow.contracts.models import Digest, Identifier, Scope
from nexusflow.modules.durable_runtime.human_completions import (
    CompletionTarget,
    SQLiteCompletionJournal,
)
from nexusflow.modules.durable_runtime.task_approval import parse_task_approval
from nexusflow.modules.durable_runtime.types import TaskCreationCall
from nexusflow.modules.human_tasks.forms import bind_task_form, prepare_task_form
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask, TaskView


class TaskCreationActivities:
    def __init__(
        self, tasks: TaskService, journal: SQLiteCompletionJournal, client: Client, scope: Scope
    ) -> None:
        if tasks.database == journal.database:
            raise ValueError("Task and completion journal databases must be separate")
        self.tasks, self.journal, self.client, self.scope = tasks, journal, client, scope
        self.route = json.dumps(
            [client.service_client.config.target_host, client.namespace], separators=(",", ":")
        )

    @activity.defn(name="nexusflow.tasks.create")
    async def create(self, call: TaskCreationCall) -> str:
        info = activity.info()
        binding = call.binding
        if (
            (binding.organization_id, binding.environment_id)
            != (self.scope.organization_id, self.scope.environment_id)
            or info.workflow_id != call.execution_id
            or info.workflow_run_id != call.temporal_run_id
            or info.workflow_namespace != self.client.namespace
        ):
            raise PermissionError("Task creation does not match trusted execution scope")
        if binding.runtime_version != "proof.v1":
            raise ValueError("Unsupported runtime version")
        for value in (
            binding.release_id,
            binding.configuration_revision,
            call.execution_id,
            call.temporal_run_id,
            call.node_invocation_id,
        ):
            TypeAdapter(Identifier).validate_python(value, strict=True)
        for digest in (binding.release_digest, call.definition_digest):
            TypeAdapter(Digest).validate_python(digest, strict=True)
        approval = parse_task_approval(call.approval_json)
        prepared = (
            prepare_task_form(
                json.dumps(approval.form.definition.model_dump(mode="json"), allow_nan=False),
                json.dumps(approval.form.initial_values, allow_nan=False),
            )
            if approval.form is not None
            else None
        )
        request = CreateTask(
            run_id=call.execution_id,
            node_invocation_id=call.node_invocation_id,
            definition_revision=binding.release_id,
            rule=approval.rule,
            user_ids=approval.user_ids,
            group_ids=approval.group_ids,
            stages=approval.stages,
        )

        def bind(task: TaskView) -> None:
            self.journal.register(
                CompletionTarget(
                    scope=self.scope,
                    task_id=task.task_id,
                    run_id=call.execution_id,
                    node_invocation_id=call.node_invocation_id,
                    definition_revision=binding.release_id,
                    workflow_id=call.execution_id,
                    temporal_run_id=call.temporal_run_id,
                    definition_digest=call.definition_digest,
                ),
                route=self.route,
            )
            if prepared is not None:
                bind_task_form(self.tasks, self.scope, task.task_id, *prepared)

        return self.tasks.create_bound(self.scope, request, bind).task_id
