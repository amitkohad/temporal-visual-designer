"""Owned, loopback-only Temporal dev server and proof worker with durable state."""

import json
import os
import re
import shutil
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
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
) -> AsyncIterator[TemporalRuntimeGateway]:
    """Run a persistent local server; installation lock must be held by caller.

    This runs only the proof interpreter: no user Python or activity dispatcher
    is registered. All database files and dispatch receipts survive shutdown.
    """
    _validate(placement, port)
    executable = _executable(temporal_executable)
    directory = directory.resolve()
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
        journal = SQLiteDispatchJournal(directory / "dispatch.db")
        try:

            async def clients(requested: Environment) -> Client:
                if requested != placement:
                    raise PermissionError(
                        "Local runtime placement does not match this installation"
                    )
                return client

            async with Worker(client, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                yield TemporalRuntimeGateway(clients, journal)
        finally:
            journal.close()
