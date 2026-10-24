"""Trusted, immutable task destinations and permanent completion acknowledgements.

Registration is a worker provisioning port, never a participant API. This adapter
does not create tasks or replace the proof runtime's manual approval controls.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from threading import RLock

from temporalio.client import Client

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.durable_runtime.types import CommandReceipt, TaskCompletionCommand
from nexusflow.modules.human_tasks.types import Completion, TaskError


class CompletionTarget(Contract):
    scope: Scope
    task_id: Identifier
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    workflow_id: Identifier
    temporal_run_id: Identifier
    definition_digest: Digest


class SQLiteCompletionJournal:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS temporal_task_completions (
            org TEXT, env TEXT, task TEXT, target TEXT NOT NULL,
            route TEXT, command TEXT, receipt TEXT,
            PRIMARY KEY(org,env,task))""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def register(self, target: CompletionTarget, *, route: str | None = None) -> None:
        target = CompletionTarget.model_validate(target)
        if target.run_id != target.workflow_id:
            raise TaskError("conflict", "Proof task execution must match Temporal workflow ID")
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO temporal_task_completions(org,env,task,target,route) VALUES(?,?,?,?,?)",
                (*self._key(target.scope, target.task_id), target.model_dump_json(), route),
            )
            if self.target(target.scope, target.task_id) != target:
                raise TaskError("conflict", "Task already has another completion destination")
            if route is not None:
                self._db.execute(
                    "UPDATE temporal_task_completions SET route=? "
                    "WHERE org=? AND env=? AND task=? AND route IS NULL",
                    (route, *self._key(target.scope, target.task_id)),
                )
                saved = self._db.execute(
                    "SELECT route FROM temporal_task_completions WHERE org=? AND env=? AND task=?",
                    self._key(target.scope, target.task_id),
                ).fetchone()
                if saved is None or saved[0] != route:
                    raise TaskError("conflict", "Task completion destination changed")

    @staticmethod
    def _key(scope: Scope, task_id: str) -> tuple[str, str, str]:
        return scope.organization_id, scope.environment_id, task_id

    def target(self, scope: Scope, task_id: str) -> CompletionTarget:
        with self._lock:
            row = self._db.execute(
                "SELECT target FROM temporal_task_completions WHERE org=? AND env=? AND task=?",
                self._key(scope, task_id),
            ).fetchone()
        if row is None:
            raise TaskError("not_found", "Task completion destination is not registered")
        return CompletionTarget.model_validate_json(row[0])

    def claim(self, target: CompletionTarget, route: str, command: str) -> str | None:
        key = self._key(target.scope, target.task_id)
        with self._lock, self._db:
            self._db.execute(
                "UPDATE temporal_task_completions SET route=?,command=? "
                "WHERE org=? AND env=? AND task=? AND command IS NULL "
                "AND (route IS NULL OR route=?)",
                (route, command, *key, route),
            )
            row = self._db.execute(
                "SELECT route,command,receipt FROM temporal_task_completions "
                "WHERE org=? AND env=? AND task=?",
                key,
            ).fetchone()
            if row is None or row[:2] != (route, command):
                raise TaskError("conflict", "Task completion content or destination changed")
            return row[2]  # type: ignore[no-any-return]

    def acknowledge(self, target: CompletionTarget, command: str, receipt: str) -> None:
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE temporal_task_completions SET receipt=? "
                "WHERE org=? AND env=? AND task=? AND command=? "
                "AND (receipt IS NULL OR receipt=?)",
                (receipt, *self._key(target.scope, target.task_id), command, receipt),
            ).rowcount
            if changed != 1:
                raise TaskError("conflict", "Task completion receipt changed")


class TemporalCompletionSink:
    def __init__(
        self,
        client: Client,
        journal: SQLiteCompletionJournal,
        scope: Scope,
        *,
        rpc_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        if rpc_timeout <= timedelta(0):
            raise ValueError("A positive RPC timeout is required")
        self.client, self.journal, self.scope = client, journal, scope
        self.rpc_timeout = rpc_timeout
        self.route = json.dumps(
            [client.service_client.config.target_host, client.namespace], separators=(",", ":")
        )

    async def deliver(self, completion: Completion) -> None:
        completion = Completion.model_validate(completion)
        if completion.scope != self.scope:
            raise PermissionError("Completion scope does not match destination")
        target = self.journal.target(self.scope, completion.task_id)
        if (
            completion.run_id,
            completion.node_invocation_id,
            completion.definition_revision,
        ) != (target.run_id, target.node_invocation_id, target.definition_revision):
            raise TaskError("conflict", "Completion does not match registered invocation")
        command = TaskCompletionCommand(
            completion.completion_id,
            completion.task_id,
            self.scope.organization_id,
            self.scope.environment_id,
            target.workflow_id,
            target.temporal_run_id,
            target.node_invocation_id,
            target.definition_digest,
            target.definition_revision,
            completion.outcome,
            completion.task_revision,
        )
        encoded = json.dumps(asdict(command), sort_keys=True, separators=(",", ":"))
        if self.journal.claim(target, self.route, encoded) is not None:
            return
        update_id = "nexusflow-task-" + hashlib.sha256(encoded.encode()).hexdigest()
        handle = self.client.get_workflow_handle(target.workflow_id, run_id=target.temporal_run_id)
        try:
            receipt = await handle.execute_update(
                "complete_task",
                command,
                id=update_id,
                result_type=CommandReceipt,
                rpc_timeout=self.rpc_timeout,
            )
        except Exception:
            # A closed execution may still retain the accepted update result.
            # Missing/expired history is failure, never evidence of delivery.
            receipt = await handle.get_update_handle(
                update_id, workflow_run_id=target.temporal_run_id, result_type=CommandReceipt
            ).result(rpc_timeout=self.rpc_timeout)
        if (
            not isinstance(receipt, CommandReceipt)
            or receipt.command_id != completion.completion_id
            or type(receipt.revision) is not int
            or receipt.revision < 1
        ):
            raise TaskError("conflict", "Temporal returned an invalid completion receipt")
        self.journal.acknowledge(
            target, encoded, json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":"))
        )
