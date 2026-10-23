"""Test-only provider contract; this fake is not an authentication boundary."""

import json
from copy import deepcopy
from typing import Protocol, runtime_checkable

import pytest
from test_contracts import load_fixture

from nexusflow.contracts.models import RequestContext, Scope
from nexusflow.contracts.validation import validate_contract


@runtime_checkable
class ProjectProvider(Protocol):
    def read(self, context: RequestContext, scope: Scope, project_id: str) -> dict: ...
    def write(
        self, context: RequestContext, scope: Scope, project_id: str, project: dict
    ) -> None: ...


class MemoryProjects:
    def __init__(self):
        self._projects = {}

    def _key(self, context, scope, project_id, permission):
        if context.scope != scope or permission not in context.resolved_permissions:
            raise PermissionError("Access denied")
        return scope.organization_id, scope.environment_id, project_id

    def read(self, context, scope, project_id):
        key = self._key(context, scope, project_id, "project.read")
        return deepcopy(self._projects[key])

    def write(self, context, scope, project_id, project):
        key = self._key(context, scope, project_id, "project.write")
        if project["organization_id"] != scope.organization_id:
            raise PermissionError("Access denied")
        self._projects[key] = deepcopy(project)


def context():
    value = validate_contract("request-context", json.dumps(load_fixture("request-context"))).value
    assert isinstance(value, RequestContext)
    return value


def test_provider_contract_and_copy_isolation():
    provider = MemoryProjects()
    assert isinstance(provider, ProjectProvider)
    actor = context()
    project = load_fixture("project")
    provider.write(actor, actor.scope, project["project_id"], project)
    project["workflows"][0]["workflow_id"] = "changed"
    first = provider.read(actor, actor.scope, project["project_id"])
    assert first["workflows"][0]["workflow_id"] == "purchase-requisition"
    first["workflows"].clear()
    assert len(provider.read(actor, actor.scope, project["project_id"])["workflows"]) == 2


@pytest.mark.parametrize(
    "other",
    [
        Scope(organization_id="other", environment_id="dev"),
        Scope(organization_id="acme", environment_id="prod"),
    ],
)
@pytest.mark.parametrize("operation", ["read", "write"])
def test_cross_scope_read_and_write_are_denied(other, operation):
    provider = MemoryProjects()
    actor = context()
    project = load_fixture("project")
    provider.write(actor, actor.scope, project["project_id"], project)
    args = (actor, other, project["project_id"])
    with pytest.raises(PermissionError, match="Access denied"):
        getattr(provider, operation)(*args, *([project] if operation == "write" else []))


@pytest.mark.parametrize("operation", ["read", "write"])
def test_missing_permissions_are_denied(operation):
    provider = MemoryProjects()
    actor = context()
    project = load_fixture("project")
    provider.write(actor, actor.scope, project["project_id"], project)
    actor = actor.model_copy(update={"resolved_permissions": ()})
    with pytest.raises(PermissionError, match="Access denied"):
        getattr(provider, operation)(
            actor, actor.scope, project["project_id"], *([project] if operation == "write" else [])
        )


def test_write_cannot_spoof_project_organization():
    actor = context()
    project = load_fixture("project")
    project["organization_id"] = "other"
    with pytest.raises(PermissionError, match="Access denied"):
        MemoryProjects().write(actor, actor.scope, project["project_id"], project)


def test_same_project_identifier_is_independent_per_environment():
    provider = MemoryProjects()
    actor = context()
    project = load_fixture("project")
    provider.write(actor, actor.scope, project["project_id"], project)
    uat = actor.model_copy(update={"scope": Scope(organization_id="acme", environment_id="uat")})
    with pytest.raises(KeyError):
        provider.read(uat, uat.scope, project["project_id"])
    project["name"] = "UAT copy"
    provider.write(uat, uat.scope, project["project_id"], project)
    assert provider.read(actor, actor.scope, project["project_id"])["name"] == "Business processes"
