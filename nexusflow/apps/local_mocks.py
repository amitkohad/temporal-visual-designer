"""Explicit synthetic Dev fixture administration; recovery checks never repair state."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator

from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID, LocalSettings, load_settings
from nexusflow.contracts.models import Contract, Scope
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant

_MARKER = "http-mock-fixtures.json"
_DATABASE = "http-mock-fixtures.db"
_GRANT = "local-mock-fixture-admin"


class LocalMockFixtureProfile(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo-http-mock-fixtures"] = "local-demo-http-mock-fixtures"
    installation_id: str
    scope: Scope
    fixtures_database: Literal["http-mock-fixtures.db"] = "http-mock-fixtures.db"

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
            raise ValueError("Local mock fixture administration supports only Dev")
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


def load_mock_fixture_profile(
    directory: Path, settings: LocalSettings
) -> LocalMockFixtureProfile | None:
    """Read bounded markers/schema only; never adopt, repair or recreate local state."""
    marker = directory / _MARKER
    database = directory / _DATABASE
    related = (database, directory / (_DATABASE + "-wal"), directory / (_DATABASE + "-shm"))
    if marker.is_symlink() or any(path.is_symlink() for path in related):
        raise ValueError("Invalid local mock fixture paths; preserve existing state") from None
    if not marker.exists():
        if any(path.exists() for path in related):
            raise ValueError(
                "Incomplete local mock fixture initialization; preserve existing state"
            )
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
            "fixtures_database",
        }:
            raise ValueError
        profile = LocalMockFixtureProfile.model_validate(document, strict=True)
        expected = Scope(organization_id=settings.organization_id, environment_id="dev")
        if profile.installation_id != settings.installation_id or profile.scope != expected:
            raise ValueError
    except Exception:
        raise ValueError("Invalid local mock fixture profile; preserve existing state") from None
    if not database.is_file():
        raise ValueError("Missing local mock fixture database; preserve existing state") from None
    try:
        # Read-only inspection includes committed WAL schema changes. Immutable
        # mode would ignore WAL and could accept a stale base-file schema.
        # SQLite may create/update locking sidecars; fixture data is never repaired.
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='http_mock_scenarios'"
            ).fetchone()
            columns = connection.execute("PRAGMA table_info(http_mock_scenarios)").fetchall()
            expected_columns = {
                "organization_id": ("TEXT", 1, 1),
                "environment_id": ("TEXT", 1, 2),
                "connection_id": ("TEXT", 1, 3),
                "connection_revision": ("TEXT", 1, 4),
                "fixture_digest": ("TEXT", 1, 0),
                "stored_bytes": ("INTEGER", 1, 0),
                "definition_json": ("TEXT", 1, 0),
            }
            actual = {row[1]: (row[2].upper(), row[3], row[5]) for row in columns}
            if (
                table is None
                or actual.keys() != expected_columns.keys()
                or any(actual.get(name) != spec for name, spec in expected_columns.items())
            ):
                raise ValueError
        finally:
            connection.close()
    except Exception:
        raise ValueError("Invalid local mock fixture database; preserve existing state") from None
    return profile


def enable_mock_fixtures(directory: Path) -> LocalMockFixtureProfile:
    """Caller holds installation lock; fixed grants are never restored on retries."""
    settings = load_settings(directory)
    existing = load_mock_fixture_profile(directory, settings)
    if existing is not None:
        return existing
    scope = Scope(organization_id=settings.organization_id, environment_id="dev")
    profile = LocalMockFixtureProfile(installation_id=settings.installation_id, scope=scope)
    identity = SQLiteIdentityRepository(directory / "identity.db")
    try:
        identity.save_grant(
            Grant(_GRANT, scope, TENANT_ID, "user", USER_ID, "organization_administrator"),
            expected_revision=0,
        )
    finally:
        identity.close()
    repository = SQLiteHTTPMockScenarios(directory / profile.fixtures_database)
    repository.close()
    # Marker last: incomplete creation remains visible and is never silently fixed.
    with (directory / _MARKER).open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    return profile
