"""Independent authorization conformance with an untrusted repository fake."""

from dataclasses import replace

import pytest

from nexusflow.contracts.models import Actor, RequestContext, Scope
from nexusflow.modules.identity_environments.policy import AuthorizationPolicy
from nexusflow.modules.identity_environments.types import (
    DirectoryBinding,
    Environment,
    Grant,
    IdentityError,
    Principal,
)

TENANT = "11111111-1111-1111-1111-111111111111"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


class Repository:
    def __init__(self, scope: Scope, grants: tuple[Grant, ...]) -> None:
        self.scope = scope
        self.records = grants
        self.member = True
        self.directory = DirectoryBinding(scope.organization_id, TENANT, "api")
        self.placement = Environment(scope, "trusted-namespace", "trusted-queue")

    def binding(self, organization_id: str) -> DirectoryBinding | None:
        return self.directory if organization_id == self.scope.organization_id else None

    def is_member(self, organization_id: str, principal: Principal) -> bool:
        return self.member and organization_id == self.scope.organization_id

    def grants(self, scope: Scope) -> tuple[Grant, ...]:
        return self.records

    def environments(self, organization_id: str) -> tuple[Environment, ...]:
        return (self.placement,) if organization_id == self.scope.organization_id else ()


def setup(role: str = "business_author", environment: str = "dev"):
    scope = Scope(organization_id="acme", environment_id=environment)
    principal = Principal(
        Actor(issuer=ISSUER, subject="subject", directory_tenant_id=TENANT), "user-1", "user"
    )
    grant = Grant("grant", scope, TENANT, "user", "user-1", role)
    repository = Repository(scope, (grant,))
    return AuthorizationPolicy(repository), repository, principal, scope


def authorize(policy, principal, scope, permission):
    return policy.authorize(
        principal, scope, permission, request_id="request", correlation_id="correlation"
    )


def denied(policy, principal, scope, permission):
    with pytest.raises(IdentityError) as error:
        authorize(policy, principal, scope, permission)
    assert error.value.code == "forbidden"
    assert str(error.value) == "Access denied"


@pytest.mark.parametrize("environment", ["dev", "uat"])
@pytest.mark.parametrize(
    "permission",
    [
        "activity.read",
        "project.read",
        "project.write",
        "package.export",
        "package.import",
        "package.activate",
        "package.rollback",
    ],
)
def test_author_permissions(environment, permission):
    policy, _, principal, scope = setup(environment=environment)
    assert permission in authorize(policy, principal, scope, permission).resolved_permissions


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_debug_is_dev_only(environment):
    policy, _, principal, scope = setup(environment=environment)
    denied(policy, principal, scope, "debug.execute")


def test_context_is_constructed_from_server_grants():
    policy, _, principal, scope = setup()
    result = authorize(policy, principal, scope, "debug.execute")
    assert result == RequestContext(
        schema_version="0.1",
        request_id="request",
        correlation_id="correlation",
        scope=scope,
        actor=principal.actor,
        resolved_permissions=(
            "activity.read",
            "debug.execute",
            "environment.read",
            "package.activate",
            "package.export",
            "package.import",
            "package.rollback",
            "project.read",
            "project.write",
            "run.read",
        ),
    )


@pytest.mark.parametrize(
    "permission",
    ["project.write", "package.import", "package.activate", "package.rollback", "activity.publish"],
)
def test_prod_author_has_no_authority(permission):
    policy, _, principal, scope = setup(environment="prod")
    denied(policy, principal, scope, permission)


@pytest.mark.parametrize("permission", ["package.import", "package.activate", "package.rollback"])
def test_production_support_can_deploy(permission):
    policy, _, principal, scope = setup("production_support", "prod")
    authorize(policy, principal, scope, permission)


@pytest.mark.parametrize(
    "permission", ["project.write", "membership.manage", "activity.publish", "debug.execute"]
)
def test_production_support_cannot_author_or_administer(permission):
    policy, _, principal, scope = setup("production_support", "prod")
    denied(policy, principal, scope, permission)


@pytest.mark.parametrize(
    ("role", "allowed", "forbidden"),
    [
        ("activity_publisher", "activity.publish", "project.write"),
        ("task_participant", "task.respond", "package.activate"),
        ("organization_administrator", "membership.manage", "project.write"),
    ],
)
def test_roles_do_not_imply_other_roles(role, allowed, forbidden):
    policy, _, principal, scope = setup(role)
    authorize(policy, principal, scope, allowed)
    denied(policy, principal, scope, forbidden)


def test_group_grants_and_membership_revocation_are_checked_each_call():
    policy, repository, principal, scope = setup()
    repository.records = (
        replace(repository.records[0], principal_kind="group", principal_id="authors"),
    )
    denied(policy, principal, scope, "project.write")
    principal = replace(principal, groups=frozenset({"authors"}))
    authorize(policy, principal, scope, "project.write")
    repository.member = False
    denied(policy, principal, scope, "project.write")


@pytest.mark.parametrize(
    "permission",
    [
        "activity.read",
        "project.read",
        "task.read",
        "run.read",
        "artifact.download",
        "events.subscribe",
        "api.invoke",
    ],
)
def test_cross_organization_resources_have_same_denial(permission):
    policy, _, principal, _ = setup()
    denied(policy, principal, Scope(organization_id="other", environment_id="dev"), permission)


@pytest.mark.parametrize(
    "mutation",
    [
        "tenant",
        "issuer",
        "environment",
        "unknown_environment",
        "membership",
        "grant_scope",
        "grant_tenant",
    ],
)
def test_untrusted_scope_and_identity_fail_closed(mutation):
    policy, repository, principal, scope = setup()
    if mutation == "tenant":
        principal = replace(
            principal, actor=principal.actor.model_copy(update={"directory_tenant_id": "other"})
        )
    elif mutation == "issuer":
        principal = replace(
            principal,
            actor=principal.actor.model_copy(update={"issuer": "https://attacker.invalid"}),
        )
    elif mutation in {"environment", "unknown_environment"}:
        scope = Scope(
            organization_id="acme",
            environment_id="uat" if mutation == "environment" else "attacker",
        )
    elif mutation == "membership":
        repository.member = False
    elif mutation == "grant_scope":
        repository.records = (
            replace(
                repository.records[0], scope=Scope(organization_id="other", environment_id="dev")
            ),
        )
    else:
        repository.records = (replace(repository.records[0], directory_tenant_id="other"),)
    denied(policy, principal, scope, "project.write")


def test_workload_does_not_inherit_user_or_group_authority():
    policy, repository, principal, scope = setup()
    principal = replace(principal, kind="workload", groups=frozenset({"authors"}))
    repository.records += (
        replace(repository.records[0], principal_kind="group", principal_id="authors"),
    )
    denied(policy, principal, scope, "project.write")
    denied(policy, principal, scope, "worker.execute")
    repository.records += (
        replace(repository.records[0], principal_kind="workload", role="worker"),
    )
    assert authorize(policy, principal, scope, "worker.execute").resolved_permissions == (
        "run.read",
        "worker.execute",
    )
    denied(policy, principal, scope, "project.write")


def test_placement_comes_from_trusted_registry():
    policy, repository, principal, scope = setup()
    assert (
        policy.placement(principal, scope, request_id="request", correlation_id="correlation")
        == repository.placement
    )


def test_worker_can_resolve_only_its_explicit_placement():
    policy, repository, principal, scope = setup()
    principal = replace(principal, kind="workload")
    repository.records = (replace(repository.records[0], principal_kind="workload", role="worker"),)
    assert (
        policy.placement(principal, scope, request_id="request", correlation_id="correlation")
        == repository.placement
    )
    with pytest.raises(IdentityError) as error:
        policy.placement(
            principal,
            Scope(organization_id="acme", environment_id="prod"),
            request_id="request",
            correlation_id="correlation",
        )
    assert error.value.code == "forbidden"
