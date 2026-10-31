"""Explicit loopback-only local-demo launcher; never a production composition root."""

import argparse
import asyncio
import json
import os
import socket
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO

import uvicorn

from nexusflow.apps.api import create_app
from nexusflow.apps.local_bootstrap import initialize, issue_token, load_settings, open_backend
from nexusflow.apps.local_debug import enable_debugger, load_debug_profile
from nexusflow.apps.local_mocks import enable_mock_fixtures, load_mock_fixture_profile
from nexusflow.apps.local_runtime import local_runtime
from nexusflow.apps.local_tasks import enable_tasks, load_task_profile
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository


class LocalCommandError(Exception):
    """Safe local command status suitable for printing without provider details."""


def _lock(handle: BinaryIO, *, unlock: bool = False) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def installation_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    # Never unlink the lock inode: another process may already have opened it.
    with (directory / ".launcher.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        try:
            _lock(handle)
        except OSError:
            raise LocalCommandError("This local installation is already in use") from None
        try:
            yield
        finally:
            _lock(handle, unlock=True)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Port must be an integer") from None
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1024 and 65535")
    return port


async def serve_local(
    directory: Path,
    *,
    api_port: int = 8000,
    temporal_port: int = 7233,
    temporal_executable: str | None = None,
) -> None:
    if (
        not 1024 <= api_port <= 65535
        or not 1024 <= temporal_port <= 65535
        or api_port == temporal_port
    ):
        raise LocalCommandError("API and Temporal require distinct valid ports")
    with installation_lock(directory):
        settings = load_settings(directory)
        task_profile = load_task_profile(directory, settings)
        load_debug_profile(directory, settings)
        load_mock_fixture_profile(directory, settings)
        identity = SQLiteIdentityRepository(directory / "identity.db")
        try:
            placement = next(
                (
                    item
                    for item in identity.environments(settings.organization_id)
                    if item.scope.environment_id == "dev"
                ),
                None,
            )
            if placement is None:
                raise LocalCommandError(
                    "Local Dev placement is unavailable; initialization is incomplete"
                )
        finally:
            identity.close()
        stop_request = directory / ".stop-request"
        stop_request.unlink(missing_ok=True)
        # Reserve the API port before starting any owned Temporal process.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name != "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", api_port))
                listener.listen(128)
                listener.setblocking(False)
            except OSError:
                raise LocalCommandError("The local API port is unavailable") from None
            async with local_runtime(
                directory,
                placement,
                temporal_executable=temporal_executable,
                port=temporal_port,
                task_profile=task_profile,
            ) as gateway:
                with open_backend(directory, gateway) as backend:
                    server = uvicorn.Server(
                        uvicorn.Config(
                            create_app(backend),
                            host="127.0.0.1",
                            port=api_port,
                            access_log=False,
                            log_level="warning",
                            proxy_headers=False,
                            server_header=False,
                        )
                    )

                    async def watch_stop() -> None:
                        while not server.should_exit:
                            if stop_request.exists():
                                try:
                                    requested = stop_request.read_text(encoding="utf-8")
                                except OSError:
                                    requested = ""
                                if requested == settings.installation_id:
                                    stop_request.unlink(missing_ok=True)
                                    server.should_exit = True
                                    return
                            await asyncio.sleep(0.1)

                    watcher = asyncio.create_task(watch_stop())
                    print(
                        json.dumps(
                            {
                                "profile": "local-demo",
                                "url": f"http://127.0.0.1:{api_port}",
                                "organization_id": settings.organization_id,
                                "environment_id": "dev",
                                "state_directory": str(directory),
                            }
                        ),
                        flush=True,
                    )
                    try:
                        await server.serve(sockets=[listener])
                    finally:
                        watcher.cancel()
                        with suppress(asyncio.CancelledError):
                            await watcher


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nexus Flow local-demo API and Temporal worker")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "init",
        "enable-tasks",
        "enable-debugger",
        "enable-mock-fixtures",
        "token",
        "serve",
        "stop",
    ):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=Path(".nexusflow-local"))
        if name == "token":
            command.add_argument("--lifetime-seconds", type=int, default=3600)
        if name == "serve":
            command.add_argument("--api-port", type=_port, default=8000)
            command.add_argument("--temporal-port", type=_port, default=7233)
            command.add_argument("--temporal-cli")
    args = parser.parse_args(argv)
    directory = args.state_dir.expanduser().resolve()
    try:
        if args.command == "init":
            with installation_lock(directory):
                settings = initialize(directory)
            print(
                json.dumps(
                    {
                        "profile": settings.profile,
                        "organization_id": settings.organization_id,
                        "environment_id": "dev",
                        "state_directory": str(directory),
                    }
                )
            )
        elif args.command == "enable-tasks":
            with installation_lock(directory):
                task_profile = enable_tasks(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "human_tasks": "enabled",
                        "organization_id": task_profile.scope.organization_id,
                        "environment_id": task_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-debugger":
            with installation_lock(directory):
                debug_profile = enable_debugger(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "debugger": "enabled",
                        "organization_id": debug_profile.scope.organization_id,
                        "environment_id": debug_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-mock-fixtures":
            with installation_lock(directory):
                mock_profile = enable_mock_fixtures(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "mock_fixtures": "enabled",
                        "administrator_role": "organization_administrator",
                        "organization_id": mock_profile.scope.organization_id,
                        "environment_id": mock_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "token":
            # Read-only signing is safe during serve; never changes grants/keys.
            print(issue_token(directory, lifetime_seconds=args.lifetime_seconds))
        elif args.command == "stop":
            settings = load_settings(directory)
            # Bounded fixed marker contains no credentials; replaces stale requests.
            (directory / ".stop-request").write_text(settings.installation_id, encoding="utf-8")
            print(json.dumps({"status": "stop_requested", "profile": "local-demo"}))
        else:
            asyncio.run(
                serve_local(
                    directory,
                    api_port=args.api_port,
                    temporal_port=args.temporal_port,
                    temporal_executable=args.temporal_cli,
                )
            )
        return 0
    except KeyboardInterrupt:
        return 0
    except LocalCommandError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print(
            "Local command failed. Check initialized local-demo state, ports and Temporal CLI configuration; existing data was preserved.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
