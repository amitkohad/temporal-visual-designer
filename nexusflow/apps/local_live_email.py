"""Explicit opt-in Dev live email profile; recovery checks never repair state."""

import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import TypeAdapter, field_validator

from nexusflow.apps.local_bootstrap import TENANT_ID, LocalSettings, load_settings
from nexusflow.apps.local_email_secrets import ensure_email_secret_map
from nexusflow.contracts.models import Contract, Identifier, Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant

_MARKER = "live-email.json"
_SECRETS = "email-secrets.json"
_GRANT = "local-live-email-worker"
_WORKLOAD = "local-live-email-worker"


class LocalLiveEmailProfile(Contract):
    schema_version: Literal["0.1"] = "0.1"
    profile: Literal["local-demo-live-email"] = "local-demo-live-email"
    installation_id: str
    scope: Scope
    secrets_file: Literal["email-secrets.json"] = "email-secrets.json"

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
            raise ValueError("Local live email supports only Dev")
        return value


class LocalLiveEmailWorkerAuthorization:
    """Injected WorkerDestinationAuthorization for local live email sends."""

    def __init__(self, scope: Scope) -> None:
        if scope.environment_id != "dev":
            raise ValueError("Local live email worker supports only Dev")
        self.scope = Scope.model_validate(
            {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
            strict=True,
        )

    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str:
        if scope != self.scope or permission != "worker.execute":
            raise PermissionError("Local live email worker access denied")
        TypeAdapter(Identifier).validate_python(resource_id, strict=True)
        return _WORKLOAD


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError


def load_live_email_profile(
    directory: Path, settings: LocalSettings
) -> LocalLiveEmailProfile | None:
    """Read bounded markers/schema only; never adopt, repair or recreate local state."""
    marker = directory / _MARKER
    secrets = directory / _SECRETS
    if marker.is_symlink() or secrets.is_symlink():
        raise ValueError("Invalid local live email paths; preserve existing state") from None
    if not marker.exists():
        if secrets.exists():
            raise ValueError("Incomplete local live email initialization; preserve existing state")
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
            "secrets_file",
        }:
            raise ValueError
        profile = LocalLiveEmailProfile.model_validate(document, strict=True)
        expected = Scope(organization_id=settings.organization_id, environment_id="dev")
        if profile.installation_id != settings.installation_id or profile.scope != expected:
            raise ValueError
    except Exception:
        raise ValueError("Invalid local live email profile; preserve existing state") from None
    if not secrets.is_file():
        raise ValueError("Missing local email secrets map; preserve existing state") from None
    try:
        with secrets.open("rb") as stream:
            body = stream.read(1048577)
        if len(body) > 1048576:
            raise ValueError
        payload = json.loads(body, object_pairs_hook=_unique, parse_constant=_constant)
        if (
            type(payload) is not dict
            or payload.keys() != {"schema_version", "secrets"}
            or payload["schema_version"] != "0.1"
            or type(payload["secrets"]) is not dict
        ):
            raise ValueError
    except Exception:
        raise ValueError("Invalid local email secrets map; preserve existing state") from None
    return profile


def enable_live_email(directory: Path) -> LocalLiveEmailProfile:
    """Caller holds installation lock; fixed grants are never restored on retries."""
    settings = load_settings(directory)
    existing = load_live_email_profile(directory, settings)
    if existing is not None:
        return existing
    scope = Scope(organization_id=settings.organization_id, environment_id="dev")
    profile = LocalLiveEmailProfile(installation_id=settings.installation_id, scope=scope)
    identity = SQLiteIdentityRepository(directory / "identity.db")
    try:
        identity.add_membership(settings.organization_id, TENANT_ID, "workload", _WORKLOAD)
        identity.save_grant(
            Grant(_GRANT, scope, TENANT_ID, "workload", _WORKLOAD, "worker"),
            expected_revision=0,
        )
    finally:
        identity.close()
    ensure_email_secret_map(directory / profile.secrets_file)
    with (directory / _MARKER).open("x", encoding="utf-8") as stream:
        stream.write(profile.model_dump_json(indent=2))
    return profile
