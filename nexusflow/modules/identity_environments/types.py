"""Frozen feature-01 provider contracts; only trusted adapters create principals."""

from dataclasses import dataclass
from typing import Literal, Protocol

from nexusflow.contracts.models import Actor, Scope

PrincipalKind = Literal["user", "group", "workload"]
Role = Literal[
    "business_author",
    "task_participant",
    "activity_publisher",
    "organization_administrator",
    "production_support",
    "worker",
]


class IdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryBinding:
    organization_id: str
    directory_tenant_id: str
    audience: str
    delegated_scope: str = "access_as_user"
    allowed_user_clients: tuple[str, ...] = ()
    allowed_workload_clients: tuple[str, ...] = ()


@dataclass(frozen=True)
class Principal:
    actor: Actor
    object_id: str
    kind: Literal["user", "workload"]
    groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Grant:
    grant_id: str
    scope: Scope
    directory_tenant_id: str
    principal_kind: PrincipalKind
    principal_id: str
    role: Role
    revision: int = 1


@dataclass(frozen=True)
class Environment:
    scope: Scope
    temporal_namespace: str
    task_queue: str


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    organization_id: str
    action: str
    resource_id: str
    actor_id: str
    correlation_id: str
    timestamp: str
    environment_id: str | None
    details_json: str


class IdentityRepository(Protocol):
    def binding(self, organization_id: str) -> DirectoryBinding | None: ...

    def is_member(self, organization_id: str, principal: Principal) -> bool: ...

    def grants(self, scope: Scope) -> tuple[Grant, ...]: ...

    def environments(self, organization_id: str) -> tuple[Environment, ...]: ...


class GroupResolver(Protocol):
    def groups(self, directory_tenant_id: str, object_id: str) -> frozenset[str]: ...


class SigningKeys(Protocol):
    def jwks(self, directory_tenant_id: str) -> dict[str, object]: ...
