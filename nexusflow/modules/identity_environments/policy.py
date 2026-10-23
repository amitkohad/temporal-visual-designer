"""Server-owned roles; token roles and caller-supplied contexts grant no authority."""

from nexusflow.contracts.models import RequestContext, Scope
from nexusflow.modules.identity_environments.types import (
    Environment,
    Grant,
    IdentityError,
    IdentityRepository,
    Principal,
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "business_author": frozenset(
        {
            "project.read",
            "project.write",
            "package.export",
            "package.import",
            "package.activate",
            "package.rollback",
            "run.read",
            "debug.execute",
            "environment.read",
        }
    ),
    "task_participant": frozenset({"task.read", "task.respond", "environment.read"}),
    "activity_publisher": frozenset(
        {"activity.read", "activity.import", "activity.publish", "environment.read"}
    ),
    "organization_administrator": frozenset(
        {"membership.manage", "connection.manage", "environment.read"}
    ),
    "production_support": frozenset(
        {
            "package.read",
            "package.import",
            "package.activate",
            "package.rollback",
            "run.read",
            "environment.read",
        }
    ),
    "worker": frozenset({"worker.execute", "run.read"}),
}


def _matches(grant: Grant, principal: Principal) -> bool:
    if grant.directory_tenant_id != principal.actor.directory_tenant_id:
        return False
    if principal.kind == "workload":
        return (
            grant.principal_kind == "workload"
            and grant.principal_id == principal.object_id
            and grant.role == "worker"
        )
    if grant.role == "worker":
        return False
    return (
        grant.principal_kind == "user"
        and grant.principal_id == principal.object_id
        or grant.principal_kind == "group"
        and grant.principal_id in principal.groups
    )


class AuthorizationPolicy:
    def __init__(self, repository: IdentityRepository) -> None:
        self.repository = repository

    def authorize(
        self,
        principal: Principal,
        scope: Scope,
        permission: str,
        *,
        request_id: str,
        correlation_id: str,
    ) -> RequestContext:
        binding = self.repository.binding(scope.organization_id)
        expected_issuer = (
            f"https://login.microsoftonline.com/{principal.actor.directory_tenant_id}/v2.0"
        )
        if (
            binding is None
            or binding.directory_tenant_id != principal.actor.directory_tenant_id
            or principal.actor.issuer != expected_issuer
            or not self.repository.is_member(scope.organization_id, principal)
        ):
            raise IdentityError("forbidden", "Access denied")
        if scope.environment_id not in {"dev", "uat", "prod"}:
            raise IdentityError("forbidden", "Access denied")
        if not any(
            environment.scope == scope
            for environment in self.repository.environments(scope.organization_id)
        ):
            raise IdentityError("forbidden", "Access denied")
        permissions: set[str] = set()
        for grant in self.repository.grants(scope):
            if grant.scope != scope or not _matches(grant, principal):
                continue
            role_permissions = ROLE_PERMISSIONS.get(grant.role, frozenset())
            if grant.role == "business_author" and scope.environment_id == "prod":
                role_permissions = frozenset()
            if scope.environment_id != "dev":
                role_permissions = role_permissions - {"debug.execute"}
            permissions.update(role_permissions)
        if permission not in permissions:
            raise IdentityError("forbidden", "Access denied")
        return RequestContext(
            schema_version="0.1",
            request_id=request_id,
            correlation_id=correlation_id,
            scope=scope,
            actor=principal.actor,
            resolved_permissions=tuple(sorted(permissions)),
        )

    def placement(
        self, principal: Principal, scope: Scope, *, request_id: str, correlation_id: str
    ) -> Environment:
        permission = "worker.execute" if principal.kind == "workload" else "environment.read"
        self.authorize(
            principal, scope, permission, request_id=request_id, correlation_id=correlation_id
        )
        return next(
            environment
            for environment in self.repository.environments(scope.organization_id)
            if environment.scope == scope
        )
