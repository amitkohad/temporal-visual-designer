"""Owned local runtime lifecycle with mocked SDK and process boundaries."""

import json
import socket
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.enums.v1 import NamespaceState
from temporalio.api.workflowservice.v1 import DescribeNamespaceResponse
from temporalio.service import RPCError, RPCStatusCode

from nexusflow.apps import local_runtime as module
from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.types import Environment


def placement():
    return Environment(Scope(organization_id="acme", environment_id="dev"), "acme-dev", "local-q")


def description(namespace="acme-dev", retention=86400, state=1):
    value = DescribeNamespaceResponse()
    value.namespace_info.name = namespace
    value.namespace_info.state = state
    value.config.workflow_execution_retention_ttl.seconds = retention
    return value


@pytest.fixture
def boundary(monkeypatch):
    events = []
    service = SimpleNamespace(
        describe_namespace=AsyncMock(return_value=description()), register_namespace=AsyncMock()
    )
    client = SimpleNamespace(
        namespace="acme-dev",
        workflow_service=service,
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="127.0.0.1:7233")),
    )

    class Server:
        def __init__(self):
            self.client = client

        async def __aenter__(self):
            events.append("server-enter")
            return self

        async def __aexit__(self, *args):
            events.append("server-exit")

    class Worker:
        def __init__(self, client_arg, **kwargs):
            self.kwargs = kwargs
            assert client_arg is client
            events.append(("worker-create", kwargs))

        async def __aenter__(self):
            events.append("worker-enter")
            return self

        async def __aexit__(self, *args):
            events.append("worker-exit")

    async def start_server(**kwargs):
        Path(kwargs["dev_server_database_filename"]).touch()
        return Server()

    start = AsyncMock(side_effect=start_server)
    monkeypatch.setattr(module.WorkflowEnvironment, "start_local", start)
    monkeypatch.setattr(module, "Worker", Worker)
    monkeypatch.setattr(module.shutil, "which", lambda value: "C:/installed/temporal.exe")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return SimpleNamespace(events=events, service=service, client=client, start=start, port=port)


@pytest.mark.asyncio
async def test_persistent_server_namespace_worker_and_journal_lifecycle(tmp_path, boundary):
    root = tmp_path / "runtime"
    async with module.local_runtime(root, placement(), port=boundary.port) as gateway:
        assert await gateway._client(placement()) is boundary.client
        dispatcher = await gateway.dispatcher(placement())
        assert dispatcher.task_queue == "local-q"
        assert dispatcher.scope == placement().scope
        assert gateway.journal.claim("execution", "route", "digest") == (True, None)
        gateway.journal.acknowledge("execution", "accepted-run")
        worker = next(item for item in boundary.events if isinstance(item, tuple))
        assert worker[1] == {"task_queue": "local-q", "workflows": [module.ProofWorkflow]}
    assert boundary.events[-2:] == ["worker-exit", "server-exit"]
    with pytest.raises(sqlite3.ProgrammingError):
        gateway.journal.claim("execution", "route", "digest")
    assert (root / "dispatch.db").exists()
    assert (root / "runtime-identity.json").exists()
    args = boundary.start.call_args.kwargs
    assert args == {
        "namespace": "acme-dev",
        "ip": "127.0.0.1",
        "port": boundary.port,
        "ui": False,
        "dev_server_existing_path": "C:/installed/temporal.exe",
        "dev_server_database_filename": str(root.resolve() / "temporal.db"),
    }
    async with module.local_runtime(root, placement(), port=boundary.port) as reopened:
        assert reopened.journal.claim("execution", "route", "digest") == (False, "accepted-run")
    boundary.service.register_namespace.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [0, -1, 1023, 65536, True, "7233"])
async def test_invalid_ports_fail_before_side_effects(tmp_path, boundary, port):
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="port"):
        async with module.local_runtime(root, placement(), port=port):
            pytest.fail("Invalid runtime entered")
    assert not root.exists()
    boundary.start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        replace(placement(), scope=Scope(organization_id="acme", environment_id="prod")),
        replace(placement(), temporal_namespace=""),
        replace(placement(), temporal_namespace="bad/namespace"),
        replace(placement(), task_queue=" "),
        replace(placement(), task_queue="queue\n"),
    ],
)
async def test_invalid_placement_fails_before_side_effects(tmp_path, boundary, value):
    root = tmp_path / "runtime"
    with pytest.raises(ValueError):
        async with module.local_runtime(root, value, port=boundary.port):
            pytest.fail("Invalid placement entered")
    assert not root.exists()
    boundary.start.assert_not_called()


@pytest.mark.asyncio
async def test_missing_executable_never_downloads_or_creates_state(tmp_path, boundary, monkeypatch):
    looked_up = []
    monkeypatch.setenv("NEXUSFLOW_TEMPORAL_CLI", "missing.exe")
    monkeypatch.setattr(module.shutil, "which", lambda value: looked_up.append(value))
    with pytest.raises(ValueError, match="never downloads"):
        async with module.local_runtime(tmp_path / "runtime", placement(), port=boundary.port):
            pytest.fail("Missing executable entered")
    assert looked_up == ["missing.exe"]
    assert not (tmp_path / "runtime").exists()
    boundary.start.assert_not_called()


def test_explicit_executable_precedes_environment_and_path(monkeypatch):
    looked_up = []
    monkeypatch.setenv("NEXUSFLOW_TEMPORAL_CLI", "environment-cli")
    monkeypatch.setattr(module.shutil, "which", lambda value: looked_up.append(value) or value)
    assert module._executable("explicit-cli") == "explicit-cli"
    assert module._executable(None) == "environment-cli"
    monkeypatch.delenv("NEXUSFLOW_TEMPORAL_CLI")
    assert module._executable(None) == "temporal"
    assert looked_up == ["explicit-cli", "environment-cli", "temporal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse", [False, True])
async def test_occupied_port_is_not_adopted(tmp_path, boundary, reuse):
    with socket.socket() as occupied:
        if reuse:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", boundary.port))
        occupied.listen()
        with pytest.raises(ValueError, match="already in use"):
            async with module.local_runtime(tmp_path / "runtime", placement(), port=boundary.port):
                pytest.fail("Existing server adopted")
    boundary.start.assert_not_called()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux TCP TIME_WAIT regression")
def test_runtime_port_probe_allows_graceful_restart_after_time_wait(tmp_path):
    # Model the owned Go listener: SO_REUSEADDR, then the server side closes
    # first so the accepted connection leaves the listening port in TIME_WAIT.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            accepted, _ = listener.accept()
            with accepted:
                accepted.settimeout(2)
                accepted.shutdown(socket.SHUT_WR)
                assert peer.recv(1) == b""
                peer.shutdown(socket.SHUT_WR)
                assert accepted.recv(1) == b""
    # Establish that this really exercises the old failing condition.
    with socket.socket() as old_probe, pytest.raises(OSError):
        old_probe.bind(("127.0.0.1", port))
    module._reserve_identity(tmp_path, placement(), port)
    assert (tmp_path / "runtime-identity.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["port", "namespace", "queue", "organization"])
async def test_persisted_destination_cannot_change(tmp_path, boundary, change):
    async with module.local_runtime(tmp_path, placement(), port=boundary.port):
        pass
    settings = placement()
    port = boundary.port
    if change == "port":
        port = 7233 if port != 7233 else 7234
    elif change == "namespace":
        settings = replace(settings, temporal_namespace="other")
    elif change == "queue":
        settings = replace(settings, task_queue="other")
    else:
        settings = replace(settings, scope=Scope(organization_id="other", environment_id="dev"))
    boundary.start.reset_mock()
    with pytest.raises(ValueError, match="identity differs"):
        async with module.local_runtime(tmp_path, settings, port=port):
            pytest.fail("Changed destination entered")
    boundary.start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["temporal.db", "dispatch.db"])
async def test_orphaned_databases_are_preserved(tmp_path, boundary, name):
    database = tmp_path / name
    database.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="no identity"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Orphan database adopted")
    assert database.read_bytes() == b"preserve"
    boundary.start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["temporal.db", "dispatch.db"])
async def test_incomplete_database_pair_fails_closed(tmp_path, boundary, name):
    async with module.local_runtime(tmp_path, placement(), port=boundary.port):
        pass
    (tmp_path / name).unlink()
    boundary.start.reset_mock()
    with pytest.raises(ValueError, match="incomplete"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Incomplete durable runtime was replaced")
    boundary.start.assert_not_called()
    assert not (tmp_path / name).exists()


@pytest.mark.asyncio
async def test_provider_rejects_other_scope_namespace_or_queue(tmp_path, boundary):
    async with module.local_runtime(tmp_path, placement(), port=boundary.port) as gateway:
        for wrong in (
            replace(placement(), scope=Scope(organization_id="other", environment_id="dev")),
            replace(placement(), temporal_namespace="other"),
            replace(placement(), task_queue="other"),
        ):
            with pytest.raises(PermissionError):
                await gateway.dispatcher(wrong)


@pytest.mark.asyncio
@pytest.mark.parametrize("register_race", [False, True])
async def test_missing_namespace_registration_and_already_exists_race(
    tmp_path, boundary, register_race
):
    boundary.service.describe_namespace.side_effect = [
        RPCError("missing", RPCStatusCode.NOT_FOUND, b""),
        description(),
    ]
    if register_race:
        boundary.service.register_namespace.side_effect = RPCError(
            "exists", RPCStatusCode.ALREADY_EXISTS, b""
        )
    async with module.local_runtime(tmp_path, placement(), port=boundary.port):
        pass
    request = boundary.service.register_namespace.call_args.args[0]
    assert request.namespace == "acme-dev"
    assert request.workflow_execution_retention_period.seconds == 86400


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["describe", "register"])
async def test_namespace_rpc_errors_are_not_swallowed(tmp_path, boundary, operation):
    denied = RPCError("denied", RPCStatusCode.PERMISSION_DENIED, b"")
    if operation == "describe":
        boundary.service.describe_namespace.side_effect = denied
    else:
        boundary.service.describe_namespace.side_effect = RPCError(
            "missing", RPCStatusCode.NOT_FOUND, b""
        )
        boundary.service.register_namespace.side_effect = denied
    with pytest.raises(RPCError) as error:
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Namespace authorization failure ignored")
    assert error.value is denied
    assert boundary.events == ["server-enter", "server-exit"]
    assert not (tmp_path / "dispatch.db").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        description(retention=86399),
        description(namespace="other"),
        description(state=NamespaceState.NAMESPACE_STATE_DELETED),
    ],
)
async def test_existing_namespace_requires_correct_name_state_and_retention(
    tmp_path, boundary, response
):
    boundary.service.describe_namespace.return_value = response
    with pytest.raises(ValueError, match="at least one day"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Unsafe namespace accepted")
    assert boundary.events == ["server-enter", "server-exit"]
    boundary.service.register_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_failed_body_closes_worker_server_and_journal(tmp_path, boundary):
    with pytest.raises(RuntimeError, match="body failed"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port) as gateway:
            raise RuntimeError("body failed")
    assert boundary.events[-2:] == ["worker-exit", "server-exit"]
    with pytest.raises(sqlite3.ProgrammingError):
        gateway.journal.claim("execution", "route", "digest")


@pytest.mark.asyncio
async def test_worker_start_failure_closes_journal_and_server(tmp_path, boundary, monkeypatch):
    journals = []
    original = module.SQLiteDispatchJournal

    def journal(path):
        value = original(path)
        journals.append(value)
        return value

    def worker(*args, **kwargs):
        raise RuntimeError("worker failed")

    monkeypatch.setattr(module, "SQLiteDispatchJournal", journal)
    monkeypatch.setattr(module, "Worker", worker)
    with pytest.raises(RuntimeError, match="worker failed"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Broken worker entered")
    assert boundary.events == ["server-enter", "server-exit"]
    with pytest.raises(sqlite3.ProgrammingError):
        journals[0].claim("execution", "route", "digest")


@pytest.mark.asyncio
async def test_failed_server_start_keeps_identity_for_retry(tmp_path, boundary):
    boundary.start.side_effect = RuntimeError("server failed")
    with pytest.raises(RuntimeError, match="server failed"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Broken server entered")
    assert json.loads((tmp_path / "runtime-identity.json").read_text())["port"] == boundary.port
    assert not (tmp_path / "dispatch.db").exists()


@pytest.mark.asyncio
async def test_unexpected_client_namespace_closes_server(tmp_path, boundary):
    boundary.client.namespace = "other"
    with pytest.raises(ValueError, match="unexpected namespace"):
        async with module.local_runtime(tmp_path, placement(), port=boundary.port):
            pytest.fail("Wrong namespace entered")
    assert boundary.events == ["server-enter", "server-exit"]
    boundary.service.describe_namespace.assert_not_called()
