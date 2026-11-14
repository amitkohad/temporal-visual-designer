"""Config-gated Entra pilot composition for Hosted Dev.

When unset, local-demo token paste continues to work unchanged. When set, the
launcher registers an organization directory binding for the configured tenant,
bootstraps author grants for listed object IDs, and exposes public SPA browser
auth parameters. Live consent and Graph credentials remain operator-owned.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope
from nexusflow.modules.identity_environments.entra_adapters import (
    ClientCredentialsTokenProvider,
    CompositeGroupResolver,
    CompositeSigningKeys,
    EmptyGroupResolver,
    MicrosoftGraphGroupResolver,
    MicrosoftJwksSigningKeys,
)
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import (
    DirectoryBinding,
    Grant,
    GroupResolver,
    SigningKeys,
)


class EntraPilotSettings(Contract):
    schema_version: Literal["0.1"] = "0.1"
    organization_id: Identifier
    directory_tenant_id: str
    spa_client_id: str
    api_audience: str
    allowed_user_client_id: str
    delegated_scope: str = "access_as_user"
    author_object_ids: tuple[str, ...] = ()
    redirect_uri: str = "http://127.0.0.1:8000/"
    graph_client_id: str | None = None
    graph_client_secret: str | None = None
    environment_id: Literal["dev"] = "dev"

    @field_validator("directory_tenant_id", "spa_client_id", "allowed_user_client_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value.lower():
            raise ValueError("Must be a canonical UUID")
        return value.lower()

    @field_validator("author_object_ids")
    @classmethod
    def author_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            if str(UUID(item)) != item.lower():
                raise ValueError("Author object IDs must be canonical UUIDs")
            normalized.append(item.lower())
        return tuple(normalized)

    @field_validator("api_audience", "delegated_scope", "redirect_uri")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip() or len(value) > 512:
            raise ValueError("Field is required")
        return value.strip()

    @field_validator("graph_client_id", "graph_client_secret")
    @classmethod
    def optional_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or len(value) > 4096:
            raise ValueError("Invalid Graph credential")
        return value.strip()


@dataclass(frozen=True)
class BrowserAuthPublicConfig:
    enabled: bool
    organization_id: str | None = None
    environment_id: str | None = None
    tenant_id: str | None = None
    spa_client_id: str | None = None
    authority: str | None = None
    scopes: tuple[str, ...] = ()
    redirect_uri: str | None = None

    def as_json(self) -> dict[str, object]:
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "organization_id": self.organization_id,
            "environment_id": self.environment_id,
            "tenant_id": self.tenant_id,
            "spa_client_id": self.spa_client_id,
            "authority": self.authority,
            "scopes": list(self.scopes),
            "redirect_uri": self.redirect_uri,
        }


def disabled_browser_auth() -> BrowserAuthPublicConfig:
    return BrowserAuthPublicConfig(enabled=False)


def browser_auth_from_settings(settings: EntraPilotSettings) -> BrowserAuthPublicConfig:
    scope = settings.delegated_scope
    if not scope.startswith("api://") and "/" not in scope:
        # SPA requests the API app's exposed scope; prefer audience/scope form.
        api_scope = f"{settings.api_audience}/{scope}"
    else:
        api_scope = scope
    return BrowserAuthPublicConfig(
        enabled=True,
        organization_id=settings.organization_id,
        environment_id=settings.environment_id,
        tenant_id=settings.directory_tenant_id,
        spa_client_id=settings.spa_client_id,
        authority=f"https://login.microsoftonline.com/{settings.directory_tenant_id}",
        scopes=(api_scope,),
        redirect_uri=settings.redirect_uri,
    )


def load_entra_pilot_settings(
    *,
    environ: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> EntraPilotSettings | None:
    """Load Entra pilot settings from JSON file and/or environment variables.

    Returns None when Entra is not configured so local-demo keeps working.
    """
    env = environ if environ is not None else dict(os.environ)
    path = config_path
    if path is None:
        raw_path = env.get("NEXUSFLOW_ENTRA_CONFIG", "").strip()
        path = Path(raw_path) if raw_path else None
    data: dict[str, object] = {}
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Entra config must be a JSON object")
        data.update(payload)
    mapping = {
        "organization_id": "NEXUSFLOW_ENTRA_ORGANIZATION_ID",
        "directory_tenant_id": "NEXUSFLOW_ENTRA_TENANT_ID",
        "spa_client_id": "NEXUSFLOW_ENTRA_SPA_CLIENT_ID",
        "api_audience": "NEXUSFLOW_ENTRA_API_AUDIENCE",
        "allowed_user_client_id": "NEXUSFLOW_ENTRA_API_CLIENT_ID",
        "delegated_scope": "NEXUSFLOW_ENTRA_DELEGATED_SCOPE",
        "redirect_uri": "NEXUSFLOW_ENTRA_REDIRECT_URI",
        "graph_client_id": "NEXUSFLOW_ENTRA_GRAPH_CLIENT_ID",
        "graph_client_secret": "NEXUSFLOW_ENTRA_GRAPH_CLIENT_SECRET",
    }
    for field, name in mapping.items():
        value = env.get(name)
        if value is not None and value.strip():
            data[field] = value.strip()
    authors = env.get("NEXUSFLOW_ENTRA_AUTHOR_OBJECT_IDS", "").strip()
    if authors:
        data["author_object_ids"] = tuple(
            part.strip() for part in authors.split(",") if part.strip()
        )
    # Presence gate: require the core fields; otherwise treat as disabled.
    required = (
        "organization_id",
        "directory_tenant_id",
        "spa_client_id",
        "api_audience",
        "allowed_user_client_id",
    )
    if not all(isinstance(data.get(name), str) and str(data[name]).strip() for name in required):
        return None
    if "allowed_user_client_id" not in data and "spa_client_id" in data:
        data["allowed_user_client_id"] = data["spa_client_id"]
    return EntraPilotSettings.model_validate(data)


def entra_directory_binding(settings: EntraPilotSettings) -> DirectoryBinding:
    return DirectoryBinding(
        organization_id=settings.organization_id,
        directory_tenant_id=settings.directory_tenant_id,
        audience=settings.api_audience,
        delegated_scope=settings.delegated_scope.split("/")[-1]
        if "/" in settings.delegated_scope
        else settings.delegated_scope,
        allowed_user_clients=(settings.allowed_user_client_id, settings.spa_client_id),
    )


def apply_entra_pilot_bootstrap(
    repository: SQLiteIdentityRepository, settings: EntraPilotSettings
) -> DirectoryBinding:
    """Idempotently register binding, Dev environments, memberships and author grants."""
    binding = entra_directory_binding(settings)
    existing = repository.binding(settings.organization_id)
    if existing is None:
        repository.register_binding(binding)
        repository.provision_environments(settings.organization_id)
    elif existing != binding:
        raise ValueError(
            "Entra organization binding conflicts with the installed identity database"
        )
    scope = Scope(organization_id=settings.organization_id, environment_id=settings.environment_id)
    for index, object_id in enumerate(settings.author_object_ids):
        repository.add_membership(
            settings.organization_id, settings.directory_tenant_id, "user", object_id
        )
        grant_id = f"entra-author-{object_id}"
        current = {grant.grant_id: grant for grant in repository.grants(scope)}
        if grant_id not in current:
            repository.save_grant(
                Grant(
                    grant_id,
                    scope,
                    settings.directory_tenant_id,
                    "user",
                    object_id,
                    "business_author",
                ),
                expected_revision=0,
            )
        _ = index  # stable ordering only
    return binding


def compose_signing_keys(
    *,
    local_tenant_id: str,
    local_keys: SigningKeys,
    entra: EntraPilotSettings | None,
) -> SigningKeys:
    if entra is None:
        return local_keys
    return CompositeSigningKeys(
        {
            local_tenant_id: local_keys,
            entra.directory_tenant_id: MicrosoftJwksSigningKeys(),
        }
    )


def compose_group_resolver(
    *,
    local_tenant_id: str,
    entra: EntraPilotSettings | None,
) -> GroupResolver | None:
    """When Graph is configured, refresh Entra groups and keep local empty.

    Without Graph credentials, return None so OID grants work for SPA tokens that
    omit group claims (ADR 0003: no resolver means signed groups must be absent).
    """
    if entra is None or not (entra.graph_client_id and entra.graph_client_secret):
        return None
    return CompositeGroupResolver(
        {
            local_tenant_id: EmptyGroupResolver(),
            entra.directory_tenant_id: MicrosoftGraphGroupResolver(
                ClientCredentialsTokenProvider(
                    client_id=entra.graph_client_id,
                    client_secret=entra.graph_client_secret,
                )
            ),
        }
    )
