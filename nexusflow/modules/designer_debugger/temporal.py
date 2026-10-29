"""Bounded, exact-run Temporal access for the Dev debugger."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import timedelta
from typing import get_args

from temporalio.client import Client, WorkflowQueryFailedError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.starts import StartError
from nexusflow.modules.durable_runtime.temporal_starts import (
    SQLiteDispatchJournal,
    TemporalStartDispatcher,
)
from nexusflow.modules.durable_runtime.types import (
    DEBUG_EVENT_PAGE_LIMIT,
    DEBUG_EVENT_RETENTION,
    CommandReceipt,
    DebugBreakpointCommand,
    DebugBreakpointState,
    DebugCommand,
    DebugEventBatch,
    DebugEventKind,
    DebugEventQuery,
    DebugEventState,
    DebugReconnectState,
    DebugRuntimeEvent,
    DebugStepOutput,
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

    async def reconnect(
        self, placement: Environment, execution_id: str, run_id: str
    ) -> DebugReconnectState:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            result = await client.get_workflow_handle(execution_id, run_id=run_id).query(
                ProofWorkflow.debug_reconnect, rpc_timeout=self.rpc_timeout
            )
            if (
                not isinstance(result, DebugReconnectState)
                or not isinstance(result.snapshot, Snapshot)
                or type(result.snapshot.revision) is not int
                or not 0 <= result.snapshot.revision < 2**63
                or result.snapshot.state not in get_args(DebugEventState)
                or type(result.snapshot.node_id) is not str
                or len(result.snapshot.node_id) > 128
                or any(
                    not (c.isascii() and (c.isalnum() or c in "_.-"))
                    for c in result.snapshot.node_id
                )
                or (not result.snapshot.node_id and result.snapshot.state != "starting")
                or type(result.snapshot.completed_nodes) is not tuple
                or len(result.snapshot.completed_nodes) > 100
                or any(
                    type(node) is not str
                    or not 1 <= len(node) <= 128
                    or any(not (c.isascii() and (c.isalnum() or c in "_.-")) for c in node)
                    for node in result.snapshot.completed_nodes
                )
                or len(set(result.snapshot.completed_nodes)) != len(result.snapshot.completed_nodes)
                or type(result.breakpoints) is not tuple
                or len(result.breakpoints) > 100
                or any(
                    type(node) is not str
                    or not 1 <= len(node) <= 128
                    or any(not (c.isascii() and (c.isalnum() or c in "_.-")) for c in node)
                    for node in result.breakpoints
                )
                or tuple(sorted(set(result.breakpoints))) != result.breakpoints
                or type(result.earliest_sequence) is not int
                or type(result.latest_sequence) is not int
                or not 0 <= result.latest_sequence < 2**63
                or result.earliest_sequence
                != (
                    max(1, result.latest_sequence - DEBUG_EVENT_RETENTION + 1)
                    if result.latest_sequence
                    else 0
                )
                or result.latest_sequence < result.snapshot.revision
            ):
                raise DebugError("unavailable", "Debug runtime returned invalid reconnect state")
            return result
        except DebugError:
            raise
        except WorkflowQueryFailedError:
            raise DebugError("conflict", "Debug reconnect is unavailable") from None
        except Exception:
            raise DebugError("unavailable", "Debug reconnect is unavailable") from None

    async def step_output(
        self, placement: Environment, execution_id: str, run_id: str, node_id: str
    ) -> DebugStepOutput:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            result = await client.get_workflow_handle(execution_id, run_id=run_id).query(
                ProofWorkflow.step_output, node_id, rpc_timeout=self.rpc_timeout
            )
            if (
                not isinstance(result, DebugStepOutput)
                or not isinstance(result.snapshot, Snapshot)
                or result.node_id != node_id
                or node_id not in result.snapshot.completed_nodes
                or type(result.output_json) is not str
                or len(result.output_json.encode("utf-8")) > 65536
            ):
                raise DebugError("unavailable", "Debug runtime returned an invalid output")
            return result
        except DebugError:
            raise
        except WorkflowQueryFailedError:
            raise DebugError("conflict", "Debug step output is unavailable") from None
        except Exception:
            raise DebugError("unavailable", "Debug step output is unavailable") from None

    async def breakpoints(
        self, placement: Environment, execution_id: str, run_id: str
    ) -> DebugBreakpointState:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            result = await client.get_workflow_handle(execution_id, run_id=run_id).query(
                ProofWorkflow.debug_breakpoints, rpc_timeout=self.rpc_timeout
            )
            if (
                not isinstance(result, DebugBreakpointState)
                or not isinstance(result.snapshot, Snapshot)
                or type(result.snapshot.revision) is not int
                or not 0 <= result.snapshot.revision < 2**63
                or type(result.breakpoints) is not tuple
                or len(result.breakpoints) > 100
                or any(
                    type(node) is not str
                    or not 1 <= len(node) <= 128
                    or any(not (c.isascii() and (c.isalnum() or c in "_.-")) for c in node)
                    for node in result.breakpoints
                )
                or tuple(sorted(set(result.breakpoints))) != result.breakpoints
            ):
                raise DebugError("unavailable", "Debug runtime returned invalid breakpoints")
            return result
        except DebugError:
            raise
        except WorkflowQueryFailedError:
            raise DebugError("conflict", "Debug breakpoints are unavailable") from None
        except Exception:
            raise DebugError("unavailable", "Debug breakpoints are unavailable") from None

    async def replace_breakpoints(
        self,
        placement: Environment,
        execution_id: str,
        run_id: str,
        command: DebugBreakpointCommand,
    ) -> CommandReceipt:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            handle = client.get_workflow_handle(execution_id, run_id=run_id)
            encoded = json.dumps(asdict(command), sort_keys=True, separators=(",", ":"))
            update_id = (
                "nexusflow-debug-breakpoints-" + hashlib.sha256(encoded.encode()).hexdigest()
            )
            try:
                receipt = await handle.execute_update(
                    ProofWorkflow.replace_breakpoints,
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
                or not 1 <= receipt.revision < 2**63
            ):
                raise DebugError("unavailable", "Debug runtime returned an invalid command receipt")
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
            raise DebugError(code, "Debug breakpoint command could not be applied") from None
        except Exception:
            raise DebugError("unavailable", "Debug breakpoint command is unavailable") from None

    async def events(
        self, placement: Environment, execution_id: str, run_id: str, query: DebugEventQuery
    ) -> DebugEventBatch:
        try:
            client = await self._client(placement)
            if not run_id:
                raise DebugError("conflict", "A pinned run is required")
            if (
                type(query.after) is not int
                or not 0 <= query.after < 2**63
                or type(query.limit) is not int
                or not 1 <= query.limit <= DEBUG_EVENT_PAGE_LIMIT
            ):
                raise DebugError("invalid_request", "Invalid debug event cursor")
            result = await client.get_workflow_handle(execution_id, run_id=run_id).query(
                ProofWorkflow.debug_events, query, rpc_timeout=self.rpc_timeout
            )
            if not isinstance(result, DebugEventBatch) or not isinstance(result.snapshot, Snapshot):
                raise ValueError("Invalid event batch")
            numbers = (result.earliest_sequence, result.latest_sequence, result.next_after)
            if (
                any(type(value) is not int or not 0 <= value < 2**63 for value in numbers)
                or type(result.events) is not tuple
                or len(result.events) > query.limit
                or type(result.has_more) is not bool
                or type(result.gap) is not bool
                or result.latest_sequence < query.after
            ):
                raise ValueError("Invalid event bounds")
            earliest = (
                max(1, result.latest_sequence - DEBUG_EVENT_RETENTION + 1)
                if result.latest_sequence
                else 0
            )
            expected_start = max(query.after + 1, earliest)
            expected_count = min(query.limit, max(0, result.latest_sequence - expected_start + 1))
            if (
                result.earliest_sequence != earliest
                or len(result.events) != expected_count
                or result.gap != bool(earliest and query.after < earliest - 1)
            ):
                raise ValueError("Invalid event continuity")
            for index, event in enumerate(result.events):
                if (
                    not isinstance(event, DebugRuntimeEvent)
                    or type(event.sequence) is not int
                    or event.sequence != expected_start + index
                    or event.kind not in get_args(DebugEventKind)
                    or event.state not in get_args(DebugEventState)
                    or type(event.revision) is not int
                    or not 0 <= event.revision < 2**63
                    or (
                        event.node_id is not None
                        and (
                            type(event.node_id) is not str
                            or not 1 <= len(event.node_id) <= 128
                            or any(
                                not (c.isascii() and (c.isalnum() or c in "_.-"))
                                for c in event.node_id
                            )
                        )
                    )
                ):
                    raise ValueError("Invalid event metadata")
            next_after = result.events[-1].sequence if result.events else query.after
            if result.next_after != next_after or result.has_more != (
                next_after < result.latest_sequence
            ):
                raise ValueError("Invalid event continuation")
            return result
        except DebugError:
            raise
        except WorkflowQueryFailedError:
            raise DebugError("conflict", "Debug events are unavailable") from None
        except Exception:
            raise DebugError("unavailable", "Debug events are unavailable") from None

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
            if command.action == "cancel":
                # Acceptance is durable before requesting Temporal cancellation. A lost
                # RPC/HTTP response retries the same update and exact-run cancellation;
                # the service saves a receipt only after both have been acknowledged.
                await handle.cancel(rpc_timeout=self.rpc_timeout)
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
