"""Immutable destination configuration contracts; values must contain no secrets."""

from dataclasses import dataclass
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, JsonValue

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope


class ConfigurationWrite(Contract):
    values: dict[str, JsonValue]


class ConfigurationMetadata(Contract):
    project_id: Identifier
    configuration_revision: Identifier
    revision: Annotated[int, Field(ge=1, lt=2**63)]
    values_digest: Digest
    actor_id: str
    created_at: str


class ConfigurationPage(Contract):
    items: tuple[ConfigurationMetadata, ...]
    next_before_revision: int | None


@dataclass(frozen=True)
class ConfigurationRecord:
    metadata: ConfigurationMetadata
    values_json: str


@runtime_checkable
class ConfigurationRepository(Protocol):
    def put(
        self,
        scope: Scope,
        project_id: str,
        configuration_revision: str,
        values_json: str,
        actor_id: str,
    ) -> ConfigurationMetadata: ...
    def get(
        self, scope: Scope, project_id: str, configuration_revision: str
    ) -> ConfigurationRecord: ...
    def list(
        self, scope: Scope, project_id: str, limit: int = 50, before_revision: int | None = None
    ) -> ConfigurationPage: ...
