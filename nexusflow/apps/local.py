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
from nexusflow.apps.live_email_worker import open_live_email_activities
from nexusflow.apps.live_http_worker import open_live_http_activities
from nexusflow.apps.local_bootstrap import initialize, issue_token, load_settings, open_backend
from nexusflow.apps.local_debug import enable_debugger, load_debug_profile
from nexusflow.apps.local_email_secrets import LocalEmailSecretStore
from nexusflow.apps.local_form_starts import enable_form_starts, load_form_start_profile
from nexusflow.apps.local_live_email import enable_live_email, load_live_email_profile
from nexusflow.apps.local_live_http import enable_live_http, load_live_http_profile
from nexusflow.apps.local_mocks import enable_mock_fixtures, load_mock_fixture_profile
from nexusflow.apps.local_runtime import local_runtime
from nexusflow.apps.local_schedules import enable_schedules, load_schedule_profile
from nexusflow.apps.local_secrets import LocalSecretStore
from nexusflow.apps.local_tasks import enable_tasks, load_task_profile
from nexusflow.apps.local_ui import (
    PilotUiError,
    attach_pilot_ui,
    require_ui_directory,
    resolve_ui_directory,
)
from nexusflow.apps.local_webhooks import enable_webhooks, load_webhook_profile
from nexusflow.modules.connectors.proof_dispatch import CombinedProofDispatch
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
    ui_directory: Path | None = None,
) -> None:
    if (
        not 1024 <= api_port <= 65535
        or not 1024 <= temporal_port <= 65535
        or api_port == temporal_port
    ):
        raise LocalCommandError("API and Temporal require distinct valid ports")
    ui_root: Path | None = None
    if ui_directory is not None:
        try:
            ui_root = require_ui_directory(ui_directory)
        except PilotUiError as error:
            raise LocalCommandError(str(error)) from None
    with installation_lock(directory):
        settings = load_settings(directory)
        task_profile = load_task_profile(directory, settings)
        load_debug_profile(directory, settings)
        mock_profile = load_mock_fixture_profile(directory, settings)
        schedule_profile = load_schedule_profile(directory, settings)
        webhook_profile = load_webhook_profile(directory, settings)
        form_start_profile = load_form_start_profile(directory, settings)
        live_http_profile = load_live_http_profile(directory, settings)
        live_email_profile = load_live_email_profile(directory, settings)
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

            async def run_gateway(live_http_activities: list | None = None) -> None:
                async with local_runtime(
                    directory,
                    placement,
                    temporal_executable=temporal_executable,
                    port=temporal_port,
                    task_profile=task_profile,
                    live_http_activities=live_http_activities,
                ) as gateway:
                    with open_backend(directory, gateway) as backend:
                        application = create_app(backend)
                        if ui_root is not None:
                            attach_pilot_ui(application, ui_root)
                        server = uvicorn.Server(
                            uvicorn.Config(
                                application,
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
                        schedule_task = None
                        if schedule_profile is not None and backend.schedules is not None:
                            from nexusflow.apps.api_backend import PlacedStartDispatcher
                            from nexusflow.apps.local_schedules import (
                                LocalScheduleFireAuthorization,
                            )
                            from nexusflow.modules.durable_runtime.schedules import (
                                AsyncScheduleService,
                                UtcClock,
                                run_schedule_poller,
                            )
                            from nexusflow.modules.durable_runtime.starts import AsyncStartService
                            from nexusflow.modules.package_promotion.service import (
                                ActivatedReleaseResolver,
                            )

                            fire_auth = LocalScheduleFireAuthorization(schedule_profile.scope)
                            fire = AsyncScheduleService(
                                backend.schedules,
                                fire_auth,
                                AsyncStartService(
                                    backend.starts,
                                    fire_auth,
                                    ActivatedReleaseResolver(backend.activations),
                                    PlacedStartDispatcher(backend.runtime, placement),
                                ),
                                backend.starts,
                                UtcClock(),
                            )
                            schedule_task = asyncio.create_task(
                                run_schedule_poller(fire, schedule_profile.scope)
                            )
                        startup = {
                            "profile": "local-demo",
                            "url": f"http://127.0.0.1:{api_port}",
                            "organization_id": settings.organization_id,
                            "environment_id": "dev",
                            "state_directory": str(directory),
                        }
                        if schedule_profile is not None:
                            startup["schedules"] = "enabled"
                        if webhook_profile is not None:
                            startup["webhooks"] = "enabled"
                        if form_start_profile is not None:
                            startup["form_starts"] = "enabled"
                        if ui_root is not None:
                            startup["ui"] = "same-origin"
                            startup["ui_directory"] = str(ui_root)
                        print(json.dumps(startup), flush=True)
                        try:
                            await server.serve(sockets=[listener])
                        finally:
                            watcher.cancel()
                            with suppress(asyncio.CancelledError):
                                await watcher
                            if schedule_task is not None:
                                schedule_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await schedule_task

            if live_http_profile is None and live_email_profile is None:
                await run_gateway()
            else:
                mock_database = (
                    None if mock_profile is None else directory / mock_profile.fixtures_database
                )
                from contextlib import ExitStack

                with ExitStack() as stack:
                    http_activities = None
                    email_activities = None
                    if live_http_profile is not None:
                        http_activities = stack.enter_context(
                            open_live_http_activities(
                                directory,
                                live_http_profile.scope,
                                placement.temporal_namespace,
                                placement.task_queue,
                                secrets_file=live_http_profile.secrets_file,
                                mock_fixtures_database=mock_database,
                            )
                        )
                    if live_email_profile is not None:
                        email_activities = stack.enter_context(
                            open_live_email_activities(
                                directory,
                                live_email_profile.scope,
                                placement.temporal_namespace,
                                placement.task_queue,
                                secrets_file=live_email_profile.secrets_file,
                            )
                        )
                    if http_activities is not None and email_activities is not None:
                        dispatch = CombinedProofDispatch(
                            http=http_activities, email=email_activities
                        ).dispatch
                    elif http_activities is not None:
                        dispatch = http_activities.dispatch
                    else:
                        assert email_activities is not None
                        dispatch = email_activities.dispatch
                    await run_gateway([dispatch])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nexus Flow local-demo API and Temporal worker")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "init",
        "enable-tasks",
        "enable-debugger",
        "enable-mock-fixtures",
        "enable-live-http",
        "enable-live-email",
        "enable-schedules",
        "enable-webhooks",
        "enable-form-starts",
        "put-secret",
        "put-smtp-secret",
        "token",
        "serve",
        "stop",
    ):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=Path(".nexusflow-local"))
        if name == "token":
            command.add_argument("--lifetime-seconds", type=int, default=3600)
        if name == "put-secret":
            command.add_argument("--reference", required=True)
            command.add_argument("--bearer-token", required=True)
        if name == "put-smtp-secret":
            command.add_argument("--reference", required=True)
            command.add_argument("--username", required=True)
            command.add_argument("--password", required=True)
        if name == "serve":
            command.add_argument("--api-port", type=_port, default=8000)
            command.add_argument("--temporal-port", type=_port, default=7233)
            command.add_argument("--temporal-cli")
            command.add_argument(
                "--ui",
                action="store_true",
                help="Serve the built React workspace from web/dist on the same origin as the API",
            )
            command.add_argument(
                "--ui-dir",
                type=Path,
                help="Built UI directory for --ui (default: ./web/dist)",
            )
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
        elif args.command == "enable-live-http":
            with installation_lock(directory):
                live_profile = enable_live_http(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "live_http": "enabled",
                        "organization_id": live_profile.scope.organization_id,
                        "environment_id": live_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-live-email":
            with installation_lock(directory):
                live_profile = enable_live_email(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "live_email": "enabled",
                        "organization_id": live_profile.scope.organization_id,
                        "environment_id": live_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-schedules":
            with installation_lock(directory):
                schedule_profile = enable_schedules(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "schedules": "enabled",
                        "organization_id": schedule_profile.scope.organization_id,
                        "environment_id": schedule_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-webhooks":
            with installation_lock(directory):
                webhook_profile = enable_webhooks(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "webhooks": "enabled",
                        "organization_id": webhook_profile.scope.organization_id,
                        "environment_id": webhook_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "enable-form-starts":
            with installation_lock(directory):
                form_start_profile = enable_form_starts(directory)
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "form_starts": "enabled",
                        "organization_id": form_start_profile.scope.organization_id,
                        "environment_id": form_start_profile.scope.environment_id,
                    }
                )
            )
        elif args.command == "put-secret":
            with installation_lock(directory):
                settings = load_settings(directory)
                live_profile = load_live_http_profile(directory, settings)
                if live_profile is None:
                    raise LocalCommandError(
                        "Enable live HTTP before storing local secret references"
                    )
                LocalSecretStore(directory / live_profile.secrets_file, live_profile.scope).put(
                    args.reference, scheme="Bearer", token=args.bearer_token
                )
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "secret_reference": args.reference,
                        "scheme": "Bearer",
                    }
                )
            )
        elif args.command == "put-smtp-secret":
            with installation_lock(directory):
                settings = load_settings(directory)
                live_profile = load_live_email_profile(directory, settings)
                if live_profile is None:
                    raise LocalCommandError(
                        "Enable live email before storing local SMTP secret references"
                    )
                LocalEmailSecretStore(
                    directory / live_profile.secrets_file, live_profile.scope
                ).put(
                    args.reference,
                    scheme="login",
                    username=args.username,
                    password=args.password,
                )
            print(
                json.dumps(
                    {
                        "profile": "local-demo",
                        "secret_reference": args.reference,
                        "scheme": "login",
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
            ui_directory = None
            if getattr(args, "ui_dir", None) is not None and not getattr(args, "ui", False):
                raise LocalCommandError("Pass --ui when using --ui-dir")
            if getattr(args, "ui", False):
                ui_directory = resolve_ui_directory(getattr(args, "ui_dir", None))
            asyncio.run(
                serve_local(
                    directory,
                    api_port=args.api_port,
                    temporal_port=args.temporal_port,
                    temporal_executable=args.temporal_cli,
                    ui_directory=ui_directory,
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
