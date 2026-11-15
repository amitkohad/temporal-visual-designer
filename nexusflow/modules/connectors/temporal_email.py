"""Opt-in live Temporal email text dispatch; never enabled by the default worker."""

import asyncio
import math
import re
from datetime import UTC, datetime
from typing import get_args

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.authorized_email_execution import EmailSendError
from nexusflow.modules.connectors.email_types import EmailTextActivityOutput
from nexusflow.modules.connectors.temporal_email_policy import prepare_email_text_call
from nexusflow.modules.connectors.temporal_email_types import (
    EmailTextActivityAdapter,
    EmailTextActivityContext,
    EmailTextDispatchError,
    EmailTextDispatchErrorCode,
)
from nexusflow.modules.durable_runtime.types import ActivityCall
from nexusflow.modules.project_versioning.canonical import canonical


class _InvalidOutput(Exception):
    pass


class EmailTextActivities:
    """Explicit worker composition for proof-workflow live email text dispatch."""

    def __init__(
        self,
        adapter: EmailTextActivityAdapter,
        scope: Scope,
        namespace: str,
        task_queue: str,
        *,
        timeout_seconds: float = 30,
    ) -> None:
        try:
            if (
                type(scope) is not Scope
                or type(namespace) is not str
                or re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", namespace) is None
                or type(task_queue) is not str
                or not 1 <= len(task_queue) <= 255
                or task_queue.strip() != task_queue
                or any(ord(char) < 32 or ord(char) > 126 for char in task_queue)
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 30
                or not math.isfinite(timeout_seconds)
            ):
                raise ValueError
            selected_scope = Scope.model_validate(
                {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
                strict=True,
            )
        except Exception:
            raise ValueError("Invalid email text worker configuration") from None
        self.adapter = adapter
        self.scope = selected_scope
        self.namespace = namespace
        self.task_queue = task_queue
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _checkpoint(deadline: asyncio.Timeout, cancellation_count: int) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > cancellation_count:
            raise asyncio.CancelledError
        expires = deadline.when()
        if deadline.expired() or (
            expires is not None and asyncio.get_running_loop().time() >= expires
        ):
            raise TimeoutError

    @staticmethod
    def _error(code: object) -> ApplicationError:
        selected = (
            code
            if type(code) is str
            and code
            in (
                *get_args(EmailTextDispatchErrorCode),
                "credentials_required",
                "not_found",
                "blocked_recipient",
                "message_too_large",
                "timeout",
                "delivery_uncertain",
                "invalid_provider_result",
            )
            else "unavailable"
        )
        return ApplicationError(
            "Email text activity failed", type="EmailTextDispatch." + selected, non_retryable=True
        )

    @staticmethod
    def _output(value: EmailTextActivityOutput, operation_id: str) -> str:
        try:
            if (
                type(value) is not EmailTextActivityOutput
                or type(value.operation_id) is not str
                or value.operation_id != operation_id
                or value.execution_mode != "live"
                or value.submission_status != "accepted"
            ):
                raise ValueError
            output = EmailTextActivityOutput.model_validate(
                {
                    "operation_id": value.operation_id,
                    "execution_mode": value.execution_mode,
                    "submission_status": value.submission_status,
                },
                strict=True,
            )
            encoded = canonical(
                {
                    "operation_id": output.operation_id,
                    "execution_mode": output.execution_mode,
                    "submission_status": output.submission_status,
                }
            )
            if len(encoded.encode("utf-8")) > 4096:
                raise ValueError
            return encoded
        except Exception:
            raise _InvalidOutput from None

    async def run(self, call: ActivityCall) -> str:
        """Shared dispatch body usable from a combined proof-dispatch owner."""
        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                self._checkpoint(deadline, cancellation_count)
                info = activity.info()
                workflow_namespace = info.workflow_namespace
                workflow_type = info.workflow_type
                workflow_id = info.workflow_id
                workflow_run_id = info.workflow_run_id
                if (
                    type(workflow_namespace) is not str
                    or type(workflow_type) is not str
                    or type(workflow_id) is not str
                    or type(workflow_run_id) is not str
                ):
                    raise EmailTextDispatchError("forbidden") from None
                context = EmailTextActivityContext(
                    namespace=info.namespace,
                    workflow_namespace=workflow_namespace,
                    task_queue=info.task_queue,
                    workflow_type=workflow_type,
                    workflow_id=workflow_id,
                    workflow_run_id=workflow_run_id,
                    activity_type=info.activity_type,
                    activity_id=info.activity_id,
                    attempt=info.attempt,
                )
                prepared = prepare_email_text_call(
                    call, context, self.scope, self.namespace, self.task_queue
                )
                self._checkpoint(deadline, cancellation_count)
                output = await self.adapter.execute(
                    prepared.scope,
                    prepared.operation_id,
                    prepared.inputs,
                    message_time=datetime.now(UTC),
                )
                self._checkpoint(deadline, cancellation_count)
                encoded = self._output(output, prepared.operation_id)
                self._checkpoint(deadline, cancellation_count)
                return encoded
        except asyncio.CancelledError:
            raise
        except _InvalidOutput:
            raise self._error("invalid_provider_result") from None
        except TimeoutError:
            raise self._error("timeout") from None
        except EmailTextDispatchError as error:
            raise self._error(error.code) from None
        except EmailSendError as error:
            raise self._error(error.code) from None
        except Exception:
            raise self._error("unavailable") from None

    @activity.defn(name="nexusflow.proof.dispatch")
    async def dispatch(self, call: ActivityCall) -> str:
        return await self.run(call)
