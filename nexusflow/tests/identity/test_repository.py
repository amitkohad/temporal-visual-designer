"""Repository persistence, isolation, and atomic write acceptance tests."""

import sqlite3
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Actor, Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import (
    DirectoryBinding,
    Grant,
    IdentityError,
    Principal,
)


def setup_repo(path):
    repo = SQLiteIdentityRepository(path)
    repo.register_binding(DirectoryBinding("org", "tenant", "audience"))
    repo.add_membership("org", "tenant", "user", "alice")
    return repo


def grant(env="dev"):
    return Grant(
        "grant",
        Scope(organization_id="org", environment_id=env),
        "tenant",
        "user",
        "alice",
        "business_author",
    )


def test_persistence_scoped_grants_and_cas(tmp_path):
    path = tmp_path / "identity.db"
    repo = setup_repo(path)
    assert repo.save_grant(grant(), expected_revision=0).revision == 1
    repo.save_grant(grant("prod"), expected_revision=0)
    with pytest.raises(IdentityError, match="revision"):
        repo.save_grant(grant(), expected_revision=0)
    assert repo.save_grant(grant(), expected_revision=1).revision == 2
    repo.revoke_grant(grant().scope, "grant", expected_revision=2)
    assert repo.grants(grant().scope) == ()
    with pytest.raises(IdentityError):
        repo.save_grant(grant(), expected_revision=0)
    assert len(repo.grants(grant("prod").scope)) == 1
    repo.close()
    reopened = SQLiteIdentityRepository(path)
    assert reopened.binding("org").directory_tenant_id == "tenant"
    assert reopened.grants(grant().scope) == ()
    assert len(reopened.audit("org")) == 6
    assert reopened.audit("other") == ()
    reopened.close()


def test_memberships_groups_and_directory_isolation(tmp_path):
    repo = setup_repo(tmp_path / "identity.db")
    actor = Actor(issuer="issuer", subject="alice", directory_tenant_id="tenant")
    principal = Principal(actor, "alice", "user")
    assert repo.is_member("org", principal)
    repo.remove_membership("org", "tenant", "user", "alice")
    assert not repo.is_member("org", principal)
    repo.add_membership("org", "tenant", "group", "finance")
    assert repo.is_member("org", replace(principal, groups=frozenset({"finance"})))
    assert not repo.is_member(
        "org", replace(principal, kind="workload", groups=frozenset({"finance"}))
    )
    assert not repo.is_member(
        "org", replace(principal, actor=actor.model_copy(update={"directory_tenant_id": "other"}))
    )
    with pytest.raises(IdentityError):
        repo.add_membership("org", "other", "user", "alice")
    with pytest.raises(IdentityError):
        repo.save_grant(grant(), expected_revision=0)
    repo.close()


def test_binding_and_environment_provision_idempotent(tmp_path):
    path = tmp_path / "identity.db"
    repo = setup_repo(path)
    repo.register_binding(DirectoryBinding("org", "tenant", "audience"))
    with pytest.raises(IdentityError):
        repo.register_binding(DirectoryBinding("org", "other", "audience"))
    environments = repo.provision_environments("org")
    assert [e.scope.environment_id for e in environments] == ["dev", "uat", "prod"]
    count = len(repo.audit("org"))
    assert repo.provision_environments("org") == environments
    assert len(repo.audit("org")) == count
    repo.register_binding(DirectoryBinding("other", "tenant", "audience"))
    other = repo.provision_environments("other")
    assert {e.temporal_namespace for e in environments}.isdisjoint(
        e.temporal_namespace for e in other
    )
    repo.close()
    reopened = SQLiteIdentityRepository(path)
    assert reopened.environments("org") == environments
    reopened.close()


def test_audit_failure_rolls_back_grant_and_all_environments(tmp_path):
    path = tmp_path / "identity.db"
    repo = setup_repo(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TRIGGER audit_failure BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_grant(grant(), expected_revision=0)
    assert repo.grants(grant().scope) == ()
    with pytest.raises(sqlite3.IntegrityError):
        repo.provision_environments("org")
    assert repo.environments("org") == ()
    connection.close()
    repo.close()


def test_concurrent_repository_connections_preserve_cas(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "identity.db"
    first = setup_repo(path)
    second = SQLiteIdentityRepository(path)

    def save(repo):
        try:
            repo.save_grant(grant(), expected_revision=0)
            return "saved"
        except IdentityError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, (first, second))) == ["conflict", "saved"]
    assert len(first.grants(grant().scope)) == 1
    first.close()
    second.close()


def test_invalid_scope_role_and_missing_binding(tmp_path):
    repo = setup_repo(tmp_path / "identity.db")
    with pytest.raises(IdentityError):
        repo.grants(Scope(organization_id="org", environment_id="arbitrary"))
    with pytest.raises(IdentityError):
        repo.provision_environments("unknown")
    with pytest.raises(IdentityError):
        repo.save_grant(replace(grant(), role="unknown"), expected_revision=0)
    with pytest.raises(IdentityError):
        repo.save_grant(grant(), expected_revision=-1)
    repo.close()


def test_group_membership_removal_suppresses_roles_despite_direct_membership(tmp_path):
    repo = setup_repo(tmp_path / "identity.db")
    repo.add_membership("org", "tenant", "group", "finance")
    group_grant = replace(grant(), principal_kind="group", principal_id="finance")
    repo.save_grant(group_grant, expected_revision=0)
    actor = Actor(issuer="issuer", subject="alice", directory_tenant_id="tenant")
    principal = Principal(actor, "alice", "user", frozenset({"finance"}))
    assert repo.is_member("org", principal)
    assert len(repo.grants(grant().scope)) == 1
    repo.remove_membership("org", "tenant", "group", "finance")
    assert repo.is_member("org", principal)
    assert repo.grants(grant().scope) == ()
    repo.add_membership("org", "tenant", "group", "finance")
    assert repo.grants(grant().scope) == (replace(group_grant, revision=1),)
    with pytest.raises(IdentityError):
        repo.save_grant(replace(group_grant, directory_tenant_id="other"), expected_revision=1)
    repo.close()
