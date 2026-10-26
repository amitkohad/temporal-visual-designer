"""Explicit synthetic local debugger profile; validate recovery state read-only."""

import sqlite3
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import field_validator

from nexusflow.apps.local_bootstrap import LocalSettings, load_settings
from nexusflow.contracts.models import Contract, Scope
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository


class LocalDebugProfile(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo-debugger"] = "local-demo-debugger"
    installation_id: str
    scope: Scope
    sessions_database: Literal["debug-sessions.db"] = "debug-sessions.db"

    @field_validator("installation_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Installation ID must be a canonical UUID")
        return value

    @field_validator("scope")
    @classmethod
    def dev_only(cls, value: Scope) -> Scope:
        if value.environment_id != "dev":
            raise ValueError("Local debugging supports only Dev")
        return value


_COLUMNS = {
    "debug_sessions": {
        "org",
        "env",
        "project",
        "operation",
        "session_id",
        "request",
        "session",
        "placement",
        "plan",
        "checksum",
    },
    "debug_commands": {"org", "env", "project", "session_id", "command_id", "request", "receipt"},
}


def load_debug_profile(directory: Path, settings: LocalSettings) -> LocalDebugProfile | None:
    """Never create, repair, or restore state while checking an optional profile."""
    marker = directory / "debugger.json"
    database = directory / "debug-sessions.db"
    if not marker.exists():
        if any(
            (directory / name).exists()
            for name in ("debug-sessions.db", "debug-sessions.db-wal", "debug-sessions.db-shm")
        ):
            raise ValueError("Incomplete local debugger initialization; preserve existing state")
        return None
    with marker.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("Local debugger profile exceeds its size limit")
    profile = LocalDebugProfile.model_validate_json(data)
    expected = Scope(organization_id=settings.organization_id, environment_id="dev")
    if profile.installation_id != settings.installation_id or profile.scope != expected:
        raise ValueError("Local debugger profile does not match this installation")
    if not database.is_file():
        raise ValueError("Missing local debugger database; preserve existing state")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for table, required in _COLUMNS.items():
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is None
            ):
                raise ValueError("Incomplete local debugger database")
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not required <= columns:
                raise ValueError("Incomplete local debugger schema")
    finally:
        connection.close()
    return profile


def enable_debugger(directory: Path) -> LocalDebugProfile:
    """Caller holds the installation lock. Never change identity or active releases."""
    settings = load_settings(directory)
    existing = load_debug_profile(directory, settings)
    if existing is not None:
        return existing
    profile = LocalDebugProfile(
        installation_id=settings.installation_id,
        scope=Scope(organization_id=settings.organization_id, environment_id="dev"),
    )
    repository = SQLiteDebugSessionRepository(directory / profile.sessions_database)
    repository.close()
    with (directory / "debugger.json").open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    return profile
