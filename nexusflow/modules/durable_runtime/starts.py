"""Durable start intent boundary; dispatchers must deduplicate by execution ID."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding


class StartError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class StartRequest:
    scope: Scope
    project_id: str
    workflow_id: str
    idempotency_key: str
    input_json: str = "{}"
    source: str = "manual"


@dataclass(frozen=True)
class StartReceipt:
    execution_id: str
    plan: ExecutionPlan
    dispatched: bool


class StartAuthorization(Protocol):
    def authorize(self, scope: Scope, project_id: str) -> None:
        """Freshly authorize a start; identity/transport adapters own policy."""
        ...


class ActiveReleaseResolver(Protocol):
    def resolve(
        self, scope: Scope, project_id: str, workflow_id: str
    ) -> tuple[ReleaseBinding, str]:
        """Atomically snapshot active binding and immutable workflow JSON."""
        ...


class StartDispatcher(Protocol):
    def start(self, execution_id: str, plan: ExecutionPlan) -> None:
        """Idempotently start or acknowledge the same immutable execution.

        Repeated calls, including after completion, must never start a new run.
        A timeout may mean the start succeeded; callers will retry this ID.
        """
        ...


class SQLiteStartRepository:
    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS runtime_starts (
            org TEXT, env TEXT, project TEXT, workflow TEXT, source TEXT, key TEXT,
            fingerprint TEXT NOT NULL, execution TEXT NOT NULL UNIQUE,
            plan TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (org, env, project, workflow, source, key))""")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _key(request: StartRequest) -> tuple[str, ...]:
        return (
            request.scope.organization_id,
            request.scope.environment_id,
            request.project_id,
            request.workflow_id,
            request.source,
            request.idempotency_key,
        )

    def lookup(self, request: StartRequest, fingerprint: str) -> StartReceipt | None:
        with self._lock:
            row = self._db.execute(
                """SELECT fingerprint, execution, plan, dispatched FROM runtime_starts
                WHERE org=? AND env=? AND project=? AND workflow=? AND source=? AND key=?""",
                self._key(request),
            ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise StartError("conflict", "Start key already used with different input")
        plan = TypeAdapter(ExecutionPlan).validate_json(row[2])
        return StartReceipt(row[1], plan, bool(row[3]))

    def reserve(self, request: StartRequest, fingerprint: str, plan: ExecutionPlan) -> StartReceipt:
        # The database unique key arbitrates independent connections/processes.
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO runtime_starts VALUES (?,?,?,?,?,?,?,?,?,0)",
                (
                    *self._key(request),
                    fingerprint,
                    "nf-" + uuid4().hex,
                    json.dumps(asdict(plan), allow_nan=False, sort_keys=True),
                ),
            )
        receipt = self.lookup(request, fingerprint)
        assert receipt is not None
        return receipt

    def acknowledge(self, execution_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE runtime_starts SET dispatched=1 WHERE execution=?", (execution_id,)
            )


class StartService:
    def __init__(
        self,
        repository: SQLiteStartRepository,
        authorization: StartAuthorization,
        releases: ActiveReleaseResolver,
        dispatcher: StartDispatcher,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.releases = releases
        self.dispatcher = dispatcher

    def start(self, request: StartRequest) -> StartReceipt:
        # Authorization precedes lookup, including retries of acknowledged starts.
        self.authorization.authorize(request.scope, request.project_id)
        try:
            for value in (request.project_id, request.workflow_id, request.idempotency_key):
                TypeAdapter(Identifier).validate_python(value)
            if request.source not in {"manual", "api"}:
                raise ValueError
            if len(request.input_json.encode()) > 65536:
                raise ValueError
            inputs = TypeAdapter(dict[str, JsonValue]).validate_json(request.input_json)
            canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValidationError, ValueError, UnicodeError, RecursionError):
            raise StartError("invalid_input", "Invalid start request") from None
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        receipt = self.repository.lookup(request, fingerprint)
        if receipt is None:
            binding, definition = self.releases.resolve(
                request.scope, request.project_id, request.workflow_id
            )
            try:
                plan = compile_plan(definition, binding, request.scope, inputs=inputs)
            except PlanError:
                raise StartError("invalid_input", "Workflow cannot be started") from None
            if plan.workflow_id != request.workflow_id:
                raise StartError("invalid_input", "Resolved workflow does not match request")
            receipt = self.repository.reserve(request, fingerprint, plan)
        if not receipt.dispatched:
            try:
                self.dispatcher.start(receipt.execution_id, receipt.plan)
            except OSError:
                raise StartError(
                    "unavailable", "Start delivery pending; retry the same key"
                ) from None
            self.repository.acknowledge(receipt.execution_id)
        return StartReceipt(receipt.execution_id, receipt.plan, True)
