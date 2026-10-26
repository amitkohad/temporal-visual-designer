"""Bounded, exact-run Temporal access for the Dev debugger."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import timedelta

from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.starts import StartError
from nexusflow.modules.durable_runtime.temporal_starts import (
    SQLiteDispatchJournal,
    TemporalStartDispatcher,
)
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    DebugCommand,
    ExecutionPlan,
    Snapshot,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment


class TemporalDebugRuntime:
    def __init__(
        self, clients: Callable[[Environment], Awaitable[Client]], journal: SQLiteDispatchJournal
    ) -> None:
        self.clients = clients
        self.journal = journal
        self.rpc_timeout = timedelta(seconds=10)

    async def _client(self, placement: Environment) -> Client:
        if placement.scope.environment_id != "dev":
            raise DebugError("forbidden", "Debugging requires Dev")
        client = await self.clients(placement)
        if client.namespace != placement.temporal_namespace:
            raise DebugError("unavailable", "Runtime placement is unavailable")
        return client

    async def start(self, placement: Environment, execution_id: str, plan: ExecutionPlan) -> str:
        try:
            client = await self._client(placement)
            if not plan.debug:
                raise DebugError("invalid_input", "A debug plan is required")
            await TemporalStartDispatcher(
                client, self.journal, placement.scope, placement.task_queue
            ).start(execution_id, plan)
            run_id = self.journal.run_id(execution_id)
            if not run_id:
                raise DebugError("unavailable", "Debug start acknowledgement is unavailable")
            return run_id
        except DebugError:
            raise
        except StartError as error:
            raise DebugError(error.code, "Debug start could not be acknowledged") from None
        except Exception:
            raise DebugError("unavailable", "Debug runtime is unavailable") from None

    async def snapshot(self, placement: Environment, execution_id: str, run_id: str) -> Snapshot:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            result = await client.get_workflow_handle(execution_id, run_id=run_id).query(
                ProofWorkflow.snapshot, rpc_timeout=self.rpc_timeout
            )
            if not isinstance(result, Snapshot):
                raise DebugError("conflict", "Debug runtime returned an invalid snapshot")
            return result
        except DebugError:
            raise
        except Exception:
            raise DebugError("unavailable", "Debug snapshot is unavailable") from None

    async def control(
        self, placement: Environment, execution_id: str, run_id: str, command: DebugCommand
    ) -> CommandReceipt:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            handle = client.get_workflow_handle(execution_id, run_id=run_id)
            encoded = json.dumps(asdict(command), sort_keys=True, separators=(",", ":"))
            update_id = "nexusflow-debug-" + hashlib.sha256(encoded.encode()).hexdigest()
            try:
                receipt = await handle.execute_update(
                    ProofWorkflow.control,
                    command,
                    id=update_id,
                    rpc_timeout=self.rpc_timeout,
                )
            except WorkflowUpdateFailedError:
                raise
            except Exception:
                receipt = await handle.get_update_handle(
                    update_id, workflow_run_id=run_id, result_type=CommandReceipt
                ).result(rpc_timeout=self.rpc_timeout)
            if (
                not isinstance(receipt, CommandReceipt)
                or receipt.command_id != command.command_id
                or type(receipt.revision) is not int
                or receipt.revision < 1
            ):
                raise DebugError("conflict", "Debug runtime returned an invalid command receipt")
            return receipt
        except DebugError:
            raise
        except WorkflowUpdateFailedError as error:
            cause = error.cause
            code = (
                "conflict"
                if isinstance(cause, ApplicationError) and cause.type == "Conflict"
                else "unavailable"
            )
            raise DebugError(code, "Debug command could not be applied") from None
        except Exception:
            raise DebugError("unavailable", "Debug control is unavailable") from None
