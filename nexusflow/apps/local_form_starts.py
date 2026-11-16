"""Explicit opt-in Dev form-start profile; recovery checks never repair state."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator

from nexusflow.apps.local_bootstrap import LocalSettings, load_settings
from nexusflow.contracts.models import Contract, Scope
from nexusflow.modules.durable_runtime.form_starts import SQLiteFormStartRepository

_MARKER = "form-starts.json"
_DATABASE = "form-starts.db"


class LocalFormStartProfile(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo-form-starts"] = "local-demo-form-starts"
    installation_id: str
    scope: Scope
    form_starts_database: Literal["form-starts.db"] = "form-starts.db"

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
            raise ValueError("Local form starts support only Dev")
        return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError


def load_form_start_profile(
    directory: Path, settings: LocalSettings
) -> LocalFormStartProfile | None:
    """Read bounded markers/schema only; never adopt, repair or recreate local state."""
    marker = directory / _MARKER
    database = directory / _DATABASE
    if marker.is_symlink() or database.is_symlink():
        raise ValueError("Invalid local form-start paths; preserve existing state") from None
    if not marker.exists():
        if database.exists():
            raise ValueError("Incomplete local form-start initialization; preserve existing state")
        return None
    try:
        with marker.open("rb") as stream:
            encoded = stream.read(16385)
        if len(encoded) > 16384:
            raise ValueError
        document = json.loads(encoded, object_pairs_hook=_unique, parse_constant=_constant)
        if type(document) is not dict or document.keys() != {
            "schema_version",
            "profile",
            "installation_id",
            "scope",
            "form_starts_database",
        }:
            raise ValueError
        profile = LocalFormStartProfile.model_validate(document, strict=True)
        expected = Scope(organization_id=settings.organization_id, environment_id="dev")
        if profile.installation_id != settings.installation_id or profile.scope != expected:
            raise ValueError
    except Exception:
        raise ValueError("Invalid local form-start profile; preserve existing state") from None
    if not database.is_file():
        raise ValueError("Missing local form-start database; preserve existing state") from None
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for table in ("runtime_form_starts", "runtime_form_start_submissions"):
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is None
            ):
                raise ValueError("Incomplete local form-start database")
    finally:
        connection.close()
    return profile


def enable_form_starts(directory: Path) -> LocalFormStartProfile:
    """Caller holds installation lock; never restores revoked identity state."""
    settings = load_settings(directory)
    existing = load_form_start_profile(directory, settings)
    if existing is not None:
        return existing
    scope = Scope(organization_id=settings.organization_id, environment_id="dev")
    profile = LocalFormStartProfile(installation_id=settings.installation_id, scope=scope)
    repository = SQLiteFormStartRepository(directory / profile.form_starts_database)
    repository.close()
    with (directory / _MARKER).open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    return profile
