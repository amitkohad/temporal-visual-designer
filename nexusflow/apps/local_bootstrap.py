"""Explicit synthetic local-demo bootstrap; never an Entra production provider."""

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jsonschema import Draft202012Validator
from pydantic import field_validator

from nexusflow.apps.api_backend import (
    ApiBackend,
    RequestAuthorization,
    RuntimeGateway,
    TemporalRuntimeGateway,
)
from nexusflow.contracts.models import Contract, DataSchema, Identifier, Scope, check_data_schema
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.types import ActivityManifest
from nexusflow.modules.durable_runtime.starts import AsyncStartDispatcher, SQLiteStartRepository
from nexusflow.modules.durable_runtime.types import Snapshot
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import DirectoryBinding, Environment, Grant
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.types import ActivationRequest
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository

TENANT_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "22222222-2222-4222-8222-222222222222"
CLIENT_ID = "33333333-3333-4333-8333-333333333333"
AUDIENCE = "api://nexus-flow-local-demo"
FILES = (
    "private-key.pem",
    "public-jwks.json",
    "identity.db",
    "projects.db",
    "catalog.db",
    "intake.db",
    "activations.db",
    "starts.db",
)


class LocalSettings(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo"] = "local-demo"
    organization_id: Identifier = "local-demo"
    configuration_revision: Identifier = "local-v1"
    installation_id: str

    @field_validator("installation_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Installation ID must be a canonical UUID")
        return value

    @field_validator("configuration_revision")
    @classmethod
    def immutable_configuration(cls, value: str) -> str:
        if value != "local-v1":
            raise ValueError("Unsupported local configuration revision")
        return value


class LocalCapabilities:
    """The bundled worker has no Python custom-activity execution provider."""

    def supports(self, scope: Scope, manifest: ActivityManifest) -> bool:
        return False


class LocalConfigurations:
    def __init__(self, settings: LocalSettings) -> None:
        self.scope = Scope(organization_id=settings.organization_id, environment_id="dev")

    def supports(self, scope: Scope, project_id: str, revision: str, schema: DataSchema) -> bool:
        if scope != self.scope or revision != "local-v1":
            return False
        try:
            check_data_schema(schema)

            # Local configuration is the immutable empty object. Never resolve references.
            def referenced(value: object) -> bool:
                if isinstance(value, dict):
                    return any(
                        k in {"$ref", "$dynamicRef"} or referenced(v) for k, v in value.items()
                    )
                return isinstance(value, list) and any(referenced(v) for v in value)

            return not referenced(schema) and Draft202012Validator(schema).is_valid({})
        except ValueError:
            return False


class LocalSigningKeys:
    def __init__(self, jwks: dict[str, object]) -> None:
        self._jwks = jwks

    def jwks(self, directory_tenant_id: str) -> dict[str, object]:
        if directory_tenant_id != TENANT_ID:
            raise ValueError("Unknown synthetic directory")
        return self._jwks


def _binding(settings: LocalSettings) -> DirectoryBinding:
    return DirectoryBinding(
        settings.organization_id, TENANT_ID, AUDIENCE, allowed_user_clients=(CLIENT_ID,)
    )


def _public(private: rsa.RSAPrivateKey, settings: LocalSettings) -> dict[str, object]:
    return {
        "keys": [
            {
                **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())),
                "kid": settings.installation_id,
                "use": "sig",
                "alg": "RS256",
            }
        ]
    }


def _private(directory: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(_read_small(directory / "private-key.pem"), None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        raise ValueError("Invalid local-demo signing key")
    return key


def _read_small(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("Local-demo metadata exceeds its size limit")
    return data


def load_settings(directory: Path) -> LocalSettings:
    try:
        settings = LocalSettings.model_validate_json(_read_small(directory / "settings.json"))
        if any(not (directory / name).is_file() for name in FILES):
            raise ValueError("Missing local-demo state")
        public = json.loads(_read_small(directory / "public-jwks.json"))
        if public != _public(_private(directory), settings):
            raise ValueError("Local-demo signing keys do not match")
        tables = {
            "identity.db": "bindings",
            "projects.db": "projects",
            "catalog.db": "activity_catalog",
            "intake.db": "activity_intake",
            "activations.db": "active_projects",
            "starts.db": "runtime_starts",
        }
        for filename, table in tables.items():
            connection = sqlite3.connect(
                (directory / filename).resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                if (
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("Incomplete local-demo database")
            finally:
                connection.close()
        # Do not recreate missing identity state or restore revoked grants.
        with ExitStack() as stack:
            repository = SQLiteIdentityRepository(directory / "identity.db")
            stack.callback(repository.close)
            if repository.binding(settings.organization_id) != _binding(settings):
                raise ValueError("Local-demo directory binding does not match settings")
        return settings
    except (OSError, ValueError, sqlite3.Error) as error:
        raise ValueError("Invalid or incomplete local-demo initialization") from error


def issue_token(directory: Path, lifetime_seconds: int = 3600) -> str:
    if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 86400:
        raise ValueError("Local token lifetime must be between 1 and 86400 seconds")
    settings = load_settings(directory)
    return _token(directory, settings, lifetime_seconds)


def _token(directory: Path, settings: LocalSettings, lifetime: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "tid": TENANT_ID,
            "oid": USER_ID,
            "sub": USER_ID,
            "ver": "2.0",
            "aud": AUDIENCE,
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "azp": CLIENT_ID,
            "scp": "access_as_user",
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "groups": [],
        },
        _private(directory),
        algorithm="RS256",
        headers={"kid": settings.installation_id},
    )


@contextmanager
def _backend(
    directory: Path,
    settings: LocalSettings,
    runtime: RuntimeGateway,
    *,
    tasks_enabled: bool = False,
    debug_enabled: bool = False,
    mock_fixtures_enabled: bool = False,
) -> Iterator[ApiBackend]:
    from nexusflow.apps.local_debug import load_debug_profile
    from nexusflow.apps.local_mocks import load_mock_fixture_profile
    from nexusflow.apps.local_tasks import LocalTaskAssignments, LocalTaskFields, load_task_profile
    from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
    from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
    from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime

    profile = load_task_profile(directory, settings) if tasks_enabled else None
    debug_profile = load_debug_profile(directory, settings) if debug_enabled else None
    mock_profile = load_mock_fixture_profile(directory, settings) if mock_fixtures_enabled else None
    if debug_profile is not None and not isinstance(runtime, TemporalRuntimeGateway):
        raise ValueError("Local debugger requires the owned Temporal runtime")
    with ExitStack() as stack:
        identity = SQLiteIdentityRepository(directory / "identity.db")
        stack.callback(identity.close)
        projects = SQLiteProjectRepository(directory / "projects.db")
        stack.callback(projects.close)
        catalog = SQLiteCatalogRepository(directory / "catalog.db")
        stack.callback(catalog.close)
        intake = SQLiteIntakeRepository(directory / "intake.db")
        stack.callback(intake.close)
        activations = SQLiteActivationRepository(directory / "activations.db")
        stack.callback(activations.close)
        starts = SQLiteStartRepository(directory / "starts.db")
        stack.callback(starts.close)
        debug_sessions = None
        debug_runtime = None
        if debug_profile is not None:
            assert isinstance(runtime, TemporalRuntimeGateway)
            debug_sessions = SQLiteDebugSessionRepository(
                directory / debug_profile.sessions_database
            )
            stack.callback(debug_sessions.close)
            debug_runtime = TemporalDebugRuntime(runtime.clients, runtime.journal)
        package_imports = SQLitePackageImportRepository(directory / "package-imports.db")
        stack.callback(package_imports.close)
        mock_fixtures = None
        if mock_profile is not None:
            mock_fixtures = SQLiteHTTPMockScenarios(directory / mock_profile.fixtures_database)
            stack.callback(mock_fixtures.close)
        yield ApiBackend(
            IdentityService(identity, LocalSigningKeys(_public(_private(directory), settings))),
            projects,
            catalog,
            intake,
            activations,
            starts,
            LocalCapabilities(),
            LocalConfigurations(settings),
            runtime,
            tasks_database=directory / profile.tasks_database if profile else None,
            task_assignments=LocalTaskAssignments(profile.scope) if profile else None,
            task_fields=LocalTaskFields(directory, profile.scope) if profile else None,
            package_imports=package_imports,
            debug_sessions=debug_sessions,
            debug_runtime=debug_runtime,
            mock_fixtures=mock_fixtures,
            mock_fixture_dev_scopes=(mock_profile.scope,) if mock_profile else (),
        )


@contextmanager
def open_backend(directory: Path, runtime: RuntimeGateway) -> Iterator[ApiBackend]:
    settings = load_settings(directory)
    with _backend(
        directory,
        settings,
        runtime,
        tasks_enabled=True,
        debug_enabled=True,
        mock_fixtures_enabled=True,
    ) as backend:
        yield backend


def _welcome() -> str:
    nodes = [
        {
            "node_id": name,
            "kind": name,
            "named_ports": {
                "inputs": [] if name == "start" else ["in"],
                "outputs": [] if name == "end" else ["out"],
            },
            "input_mapping": {"seconds": 1} if name == "wait" else {},
        }
        for name in ("start", "wait", "end")
    ]
    return json.dumps(
        {
            "schema_version": "0.1",
            "workflows": [
                {
                    "schema_version": "0.1",
                    "workflow_id": "welcome",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "nodes": nodes,
                    "start_definitions": [
                        {"start_id": "manual", "node_id": "start", "kind": "manual"}
                    ],
                    "edges": [
                        {
                            "source_node_id": a,
                            "source_port": "out",
                            "target_node_id": b,
                            "target_port": "in",
                        }
                        for a, b in (("start", "wait"), ("wait", "end"))
                    ],
                    "error_routes": [],
                }
            ],
        }
    )


class _InitializationRuntime:
    async def dispatcher(self, placement: Environment) -> AsyncStartDispatcher:
        raise RuntimeError("Initialization cannot dispatch workflows")

    async def snapshot(self, placement: Environment, execution_id: str) -> Snapshot:
        raise RuntimeError("Initialization cannot query workflows")


def initialize(directory: Path) -> LocalSettings:
    """Create a new explicit demo; the caller serializes initialization with its lock."""
    if (directory / "settings.json").exists():
        return load_settings(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # The launcher may hold its exclusive initialization lock in this directory.
    if any(path.name != ".launcher.lock" for path in directory.iterdir()):
        raise ValueError("Directory is populated or initialization is incomplete")
    settings = LocalSettings(installation_id=str(uuid4()))
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    descriptor = os.open(directory / "private-key.pem", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    (directory / "private-key.pem").chmod(0o600)
    with (directory / "public-jwks.json").open("x", encoding="utf-8") as stream:
        json.dump(_public(private, settings), stream)
    identity = SQLiteIdentityRepository(directory / "identity.db")
    try:
        identity.register_binding(_binding(settings))
        identity.add_membership(settings.organization_id, TENANT_ID, "user", USER_ID)
        identity.provision_environments(settings.organization_id)
        identity.save_grant(
            Grant(
                "local-author",
                Scope(organization_id=settings.organization_id, environment_id="dev"),
                TENANT_ID,
                "user",
                USER_ID,
                "business_author",
            ),
            expected_revision=0,
        )
    finally:
        identity.close()
    # No runtime operations are used when publishing/activating the seed.

    with _backend(directory, settings, _InitializationRuntime()) as backend:
        authorization = RequestAuthorization(
            backend.identity, _token(directory, settings, 300), "bootstrap"
        )
        scope = Scope(organization_id=settings.organization_id, environment_id="dev")
        service = backend.project_service(authorization, scope)
        revision = service.create(scope, "welcome", "Welcome to Nexus Flow Designer", _welcome())
        release = service.publish(scope, "welcome", revision.number, "welcome-release")
        backend.activation_service(authorization).apply(
            scope,
            ActivationRequest(
                project_id="welcome",
                release_id=release.release_id,
                configuration_revision=settings.configuration_revision,
                operation_id="welcome-activation",
                expected_revision=0,
            ),
        )
    with (directory / "settings.json").open("x", encoding="utf-8") as stream:
        stream.write(settings.model_dump_json(indent=2))
    return settings
