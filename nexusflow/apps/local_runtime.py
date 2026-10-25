"""Owned, loopback-only Temporal dev server and proof worker with durable state."""

import json
import os
import re
import shutil
import socket
import sqlite3
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager, closing
from datetime import timedelta
from pathlib import Path

from google.protobuf.duration_pb2 import Duration
from temporalio.api.enums.v1 import NamespaceState
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nexusflow.apps.api_backend import TemporalRuntimeGateway
from nexusflow.apps.local_bootstrap import load_settings
from nexusflow.apps.local_tasks import (
    LocalTaskAssignments,
    LocalTaskProfile,
    LocalTaskWorkerAuthorization,
    load_task_profile,
)
from nexusflow.modules.durable_runtime.human_completions import (
    SQLiteCompletionJournal,
    TemporalCompletionSink,
)
from nexusflow.modules.durable_runtime.task_creation import TaskCreationActivities
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.dispatch import CompletionDispatcher, run_completion_dispatcher
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.identity_environments.types import Environment


def _validate(placement: Environment, port: int) -> None:
    if placement.scope.environment_id != "dev":
        raise ValueError("The local launcher supports only the Dev environment (dev)")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Temporal port must be an integer between 1024 and 65535")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", placement.temporal_namespace):
        raise ValueError("A valid local Temporal namespace is required")
    if (
        not placement.task_queue.strip()
        or len(placement.task_queue) > 255
        or any(ord(char) < 32 for char in placement.task_queue)
    ):
        raise ValueError("A valid local Temporal task queue is required")


def _executable(explicit: str | None) -> str:
    configured = explicit if explicit is not None else os.environ.get("NEXUSFLOW_TEMPORAL_CLI")
    executable = shutil.which(configured if configured is not None else "temporal")
    if not executable:
        raise ValueError(
            "Install Temporal CLI 1.9.1 and set NEXUSFLOW_TEMPORAL_CLI to its executable, "
            "or place temporal on PATH; the local launcher never downloads a CLI"
        )
    return executable


def _reserve_identity(directory: Path, placement: Environment, port: int) -> None:
    """The caller holds the installation lock; never repoint existing journals."""
    identity = {
        "version": 1,
        "host": "127.0.0.1",
        "port": port,
        "organization": placement.scope.organization_id,
        "environment": placement.scope.environment_id,
        "namespace": placement.temporal_namespace,
        "task_queue": placement.task_queue,
    }
    marker = directory / "runtime-identity.json"
    if marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise ValueError(
                "Local runtime identity is unreadable; preserve it for recovery"
            ) from error
        if previous != identity:
            raise ValueError("Existing local runtime identity differs; use the original settings")
        if (directory / "temporal.db").exists() != (directory / "dispatch.db").exists():
            raise ValueError(
                "Local Temporal and dispatch databases are incomplete; preserve both for recovery"
            )
    elif any((directory / name).exists() for name in ("temporal.db", "dispatch.db")):
        raise ValueError("Existing runtime databases have no identity; preserve them for recovery")
    # Fail before starting the SDK, so an unrelated server is never adopted.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            # Go's POSIX listener enables reuse; match it so a graceful restart
            # can bind past accepted connections in TIME_WAIT. A live listener
            # still rejects this bind because SO_REUSEPORT is never enabled.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise ValueError(
                "Temporal loopback port is already in use; stop its owner first"
            ) from error
    if not marker.exists():
        directory.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(identity, stream, sort_keys=True)
            stream.write("\n")


async def _namespace(client: Client, namespace: str) -> None:
    service = client.workflow_service
    request = DescribeNamespaceRequest(namespace=namespace)
    try:
        description = await service.describe_namespace(request, timeout=timedelta(seconds=10))
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        try:
            await service.register_namespace(
                RegisterNamespaceRequest(
                    namespace=namespace,
                    workflow_execution_retention_period=Duration(seconds=86400),
                ),
                timeout=timedelta(seconds=10),
            )
        except RPCError as register_error:
            if register_error.status != RPCStatusCode.ALREADY_EXISTS:
                raise
        description = await service.describe_namespace(request, timeout=timedelta(seconds=10))
    if (
        description.namespace_info.name != namespace
        or description.namespace_info.state != NamespaceState.NAMESPACE_STATE_REGISTERED
        or description.config.workflow_execution_retention_ttl.ToTimedelta() < timedelta(days=1)
    ):
        raise ValueError(
            "Local Temporal namespace must be registered with at least one day retention"
        )


@asynccontextmanager
async def local_runtime(
    directory: Path,
    placement: Environment,
    *,
    temporal_executable: str | None = None,
    port: int = 7233,
    task_profile: LocalTaskProfile | None = None,
) -> AsyncIterator[TemporalRuntimeGateway]:
    """Run a persistent local server; installation lock must be held by caller.

    The optional synthetic task profile registers managed approval creation and
    owns completion delivery. No custom Python activities are executed.
    """
    _validate(placement, port)
    executable = _executable(temporal_executable)
    directory = directory.resolve()
    if task_profile is not None:
        settings = load_settings(directory)
        if (
            load_task_profile(directory, settings) != task_profile
            or task_profile.scope != placement.scope
        ):
            raise ValueError("Local task profile does not match this runtime")
        # Read-only inspection must precede every writable journal constructor.
        with closing(
            sqlite3.connect((directory / "identity.db").as_uri() + "?mode=ro", uri=True)
        ) as identity:
            actual = identity.execute(
                "SELECT namespace,queue FROM environments WHERE org=? AND env=?",
                (placement.scope.organization_id, placement.scope.environment_id),
            ).fetchone()
        if actual != (placement.temporal_namespace, placement.task_queue):
            raise ValueError("Local task runtime placement differs from persisted Dev placement")
    _reserve_identity(directory, placement, port)
    async with await WorkflowEnvironment.start_local(
        namespace=placement.temporal_namespace,
        ip="127.0.0.1",
        port=port,
        ui=False,
        dev_server_existing_path=executable,
        dev_server_database_filename=str(directory / "temporal.db"),
    ) as environment:
        client = environment.client
        if client.namespace != placement.temporal_namespace:
            raise ValueError("Local Temporal client has an unexpected namespace")
        await _namespace(client, placement.temporal_namespace)
        with ExitStack() as resources:
            journal = SQLiteDispatchJournal(directory / "dispatch.db")
            resources.callback(journal.close)

            async def clients(requested: Environment) -> Client:
                if requested != placement:
                    raise PermissionError(
                        "Local runtime placement does not match this installation"
                    )
                return client

            if task_profile is None:
                async with Worker(
                    client, task_queue=placement.task_queue, workflows=[ProofWorkflow]
                ):
                    yield TemporalRuntimeGateway(clients, journal)
            else:
                tasks = TaskService(
                    directory / task_profile.tasks_database,
                    LocalTaskWorkerAuthorization(placement.scope),
                    LocalTaskAssignments(placement.scope),
                )
                resources.callback(tasks.close)
                completions = SQLiteCompletionJournal(directory / task_profile.completion_database)
                resources.callback(completions.close)
                creation = TaskCreationActivities(tasks, completions, client, placement.scope)
                dispatcher = CompletionDispatcher(
                    tasks,
                    placement.scope,
                    TemporalCompletionSink(client, completions, placement.scope),
                )
                async with Worker(
                    client,
                    task_queue=placement.task_queue,
                    workflows=[ProofWorkflow],
                    activities=[creation.create],
                ):
                    async with run_completion_dispatcher(dispatcher):
                        yield TemporalRuntimeGateway(clients, journal)
