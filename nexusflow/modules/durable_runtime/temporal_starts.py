"""Conservative, permanently journaled dispatch to the Temporal proof runtime."""

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from threading import RLock

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from nexusflow.contracts.models import Scope
from nexusflow.modules.durable_runtime.starts import StartError
from nexusflow.modules.durable_runtime.types import ExecutionPlan

WORKFLOW_TYPE = "NexusFlowProofV1"


class SQLiteDispatchJournal:
    """Private, non-expiring records; never delete claims to retry a start."""

    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS temporal_dispatches (
            execution TEXT PRIMARY KEY, route TEXT NOT NULL, digest TEXT NOT NULL,
            run_id TEXT)""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def claim(self, execution: str, route: str, digest: str) -> tuple[bool, str | None]:
        with self._lock, self._db:
            inserted = (
                self._db.execute(
                    "INSERT OR IGNORE INTO temporal_dispatches VALUES (?,?,?,NULL)",
                    (execution, route, digest),
                ).rowcount
                == 1
            )
            row = self._db.execute(
                "SELECT route, digest, run_id FROM temporal_dispatches WHERE execution=?",
                (execution,),
            ).fetchone()
            assert row is not None
            if (row[0], row[1]) != (route, digest):
                raise StartError("conflict", "Execution ID has another plan or destination")
            return inserted, row[2]

    def acknowledge(self, execution: str, run_id: str) -> None:
        if not run_id:
            raise StartError("unavailable", "Temporal did not return a run ID")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT run_id FROM temporal_dispatches WHERE execution=?", (execution,)
            ).fetchone()
            if row is None or row[0] not in (None, run_id):
                raise StartError("conflict", "Execution has another run ID")
            self._db.execute(
                "UPDATE temporal_dispatches SET run_id=? WHERE execution=?", (run_id, execution)
            )


class TemporalStartDispatcher:
    def __init__(
        self,
        client: Client,
        journal: SQLiteDispatchJournal,
        scope: Scope,
        task_queue: str,
        *,
        rpc_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        if not task_queue.strip() or len(task_queue) > 255 or rpc_timeout <= timedelta(0):
            raise ValueError("A task queue and positive RPC timeout are required")
        self.client = client
        self.journal = journal
        self.scope = scope
        self.task_queue = task_queue
        self.rpc_timeout = rpc_timeout
        # Journal receipts cannot silently move between server destinations.
        self.route = json.dumps(
            [
                client.service_client.config.target_host,
                client.namespace,
                task_queue,
                scope.organization_id,
                scope.environment_id,
            ],
            separators=(",", ":"),
        )

    async def start(self, execution_id: str, plan: ExecutionPlan) -> None:
        if (plan.binding.organization_id, plan.binding.environment_id) != (
            self.scope.organization_id,
            self.scope.environment_id,
        ):
            raise PermissionError("Execution scope does not match dispatcher")
        if (
            not re.fullmatch(r"nf-[a-f0-9]{32}", execution_id)
            or plan.binding.runtime_version != "proof.v1"
        ):
            raise StartError("invalid_input", "Unsupported execution ID or interpreter")
        canonical = json.dumps(asdict(plan), sort_keys=True, separators=(",", ":"), allow_nan=False)
        claimed, run_id = self.journal.claim(
            execution_id, self.route, hashlib.sha256(canonical.encode()).hexdigest()
        )
        if run_id is not None:
            return
        try:
            if claimed:
                try:
                    handle = await self.client.start_workflow(
                        WORKFLOW_TYPE,
                        plan,
                        id=execution_id,
                        task_queue=self.task_queue,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                        rpc_timeout=self.rpc_timeout,
                    )
                except WorkflowAlreadyStartedError:
                    pass
                else:
                    if handle.run_id is None:
                        raise OSError("Start acknowledgement has no run ID")
                    self.journal.acknowledge(execution_id, handle.run_id)
                    return
            # Only the winning claimant may issue StartWorkflowExecution. On
            # ambiguous retries inspect history, never reissue a start after
            # Temporal retention might have removed the original execution.
            await self._reconcile(execution_id, plan)
        except (RPCError, TimeoutError) as error:
            raise OSError("Temporal start acknowledgement is unavailable") from error

    async def _reconcile(self, execution_id: str, plan: ExecutionPlan) -> None:
        description = await self.client.get_workflow_handle(execution_id).describe(
            rpc_timeout=self.rpc_timeout
        )
        handle = self.client.get_workflow_handle(execution_id, run_id=description.run_id)
        events = handle.fetch_history_events(page_size=1, rpc_timeout=self.rpc_timeout)
        try:
            event = await anext(events)
        except StopAsyncIteration:
            raise StartError("conflict", "Execution start history is missing") from None
        if not event.HasField("workflow_execution_started_event_attributes"):
            raise StartError("conflict", "Execution start history is invalid")
        started = event.workflow_execution_started_event_attributes
        if (
            started.workflow_type.name != WORKFLOW_TYPE
            or started.task_queue.name != self.task_queue
        ):
            raise StartError("conflict", "Execution belongs to another workflow or queue")
        try:
            values = await self.client.data_converter.decode(
                started.input.payloads, [ExecutionPlan]
            )
        except Exception:
            raise StartError("conflict", "Execution start payload cannot be verified") from None
        if values != [plan]:
            raise StartError("conflict", "Execution has another immutable plan")
        self.journal.acknowledge(execution_id, description.run_id)
