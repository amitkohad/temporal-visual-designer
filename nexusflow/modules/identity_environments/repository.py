"""Transactional SQLite identity metadata, scoped to organization and environment."""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.types import (
    AuditEvent,
    DirectoryBinding,
    Environment,
    Grant,
    IdentityError,
    Principal,
    PrincipalKind,
    Role,
)


class SQLiteIdentityRepository:
    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS bindings (org TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memberships (
                org TEXT NOT NULL, tenant TEXT NOT NULL, kind TEXT NOT NULL, principal TEXT NOT NULL,
                PRIMARY KEY (org, tenant, kind, principal));
            CREATE TABLE IF NOT EXISTS grants (
                org TEXT NOT NULL, env TEXT NOT NULL, id TEXT NOT NULL, tenant TEXT NOT NULL,
                kind TEXT NOT NULL, principal TEXT NOT NULL, role TEXT NOT NULL,
                revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (org, env, id));
            CREATE TABLE IF NOT EXISTS environments (
                org TEXT NOT NULL, env TEXT NOT NULL, namespace TEXT NOT NULL UNIQUE,
                queue TEXT NOT NULL UNIQUE, PRIMARY KEY (org, env));
            CREATE TABLE IF NOT EXISTS audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT NOT NULL,
                action TEXT NOT NULL, resource TEXT NOT NULL, actor TEXT NOT NULL,
                correlation TEXT NOT NULL, timestamp TEXT NOT NULL,
                env TEXT, details TEXT NOT NULL);
        """)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # Acquire the SQLite write reservation before any CAS read, including across
        # independent repository instances connected to the same database.
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _identifier(value: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
            raise IdentityError("invalid_input", "Invalid identifier")

    def _audit(
        self,
        org: str,
        action: str,
        resource: str,
        actor: str,
        correlation: str,
        *,
        env: str | None = None,
        details: str = "{}",
    ) -> None:
        self._identifier(actor)
        self._identifier(correlation)
        self._db.execute(
            "INSERT INTO audit (org,action,resource,actor,correlation,timestamp,env,details) VALUES (?,?,?,?,?,?,?,?)",
            (
                org,
                action,
                resource,
                actor,
                correlation,
                datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                env,
                details,
            ),
        )

    def binding(self, organization_id: str) -> DirectoryBinding | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM bindings WHERE org=?", (organization_id,)
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            data["allowed_user_clients"] = tuple(data["allowed_user_clients"])
            data["allowed_workload_clients"] = tuple(data["allowed_workload_clients"])
            return DirectoryBinding(**data)

    def _tenant(self, org: str, tenant: str | None = None) -> DirectoryBinding:
        self._identifier(org)
        binding = self.binding(org)
        if binding is None or (tenant is not None and tenant != binding.directory_tenant_id):
            raise IdentityError("forbidden", "Directory binding does not match")
        return binding

    def register_binding(
        self,
        binding: DirectoryBinding,
        *,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> None:
        self._identifier(binding.organization_id)
        self._identifier(binding.directory_tenant_id)
        if not binding.audience.strip() or not binding.delegated_scope.strip():
            raise IdentityError("invalid_input", "Audience and delegated scope are required")
        for client in (*binding.allowed_user_clients, *binding.allowed_workload_clients):
            self._identifier(client)
        with self._transaction():
            existing = self.binding(binding.organization_id)
            if existing is not None:
                if existing != binding:
                    raise IdentityError("conflict", "Directory mapping already exists")
                return
            self._db.execute(
                "INSERT INTO bindings VALUES (?,?)",
                (binding.organization_id, json.dumps(asdict(binding))),
            )
            self._audit(
                binding.organization_id,
                "binding.register",
                binding.organization_id,
                actor_id,
                correlation_id,
            )

    def _membership(
        self,
        org: str,
        tenant: str,
        kind: PrincipalKind,
        principal: str,
        add: bool,
        actor: str,
        correlation: str,
    ) -> None:
        self._tenant(org, tenant)
        self._identifier(principal)
        if kind not in get_args(PrincipalKind):
            raise IdentityError("invalid_input", "Invalid principal kind")
        with self._transaction():
            if add:
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO memberships VALUES (?,?,?,?)",
                    (org, tenant, kind, principal),
                )
            else:
                cursor = self._db.execute(
                    "DELETE FROM memberships WHERE org=? AND tenant=? AND kind=? AND principal=?",
                    (org, tenant, kind, principal),
                )
            if cursor.rowcount:
                self._audit(
                    org,
                    "membership.add" if add else "membership.remove",
                    principal,
                    actor,
                    correlation,
                    details=json.dumps({"kind": kind, "tenant": tenant}, sort_keys=True),
                )

    def add_membership(
        self,
        organization_id: str,
        directory_tenant_id: str,
        principal_kind: PrincipalKind,
        principal_id: str,
        *,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> None:
        self._membership(
            organization_id,
            directory_tenant_id,
            principal_kind,
            principal_id,
            True,
            actor_id,
            correlation_id,
        )

    def remove_membership(
        self,
        organization_id: str,
        directory_tenant_id: str,
        principal_kind: PrincipalKind,
        principal_id: str,
        *,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> None:
        self._membership(
            organization_id,
            directory_tenant_id,
            principal_kind,
            principal_id,
            False,
            actor_id,
            correlation_id,
        )

    def is_member(self, organization_id: str, principal: Principal) -> bool:
        with self._lock:
            binding = self.binding(organization_id)
            if (
                binding is None
                or binding.directory_tenant_id != principal.actor.directory_tenant_id
            ):
                return False
            rows = self._db.execute(
                "SELECT kind,principal FROM memberships WHERE org=? AND tenant=?",
                (organization_id, binding.directory_tenant_id),
            ).fetchall()
            return any(
                (kind == principal.kind and identifier == principal.object_id)
                or (principal.kind == "user" and kind == "group" and identifier in principal.groups)
                for kind, identifier in rows
            )

    @staticmethod
    def _scope(scope: Scope) -> None:
        if scope.environment_id not in ("dev", "uat", "prod"):
            raise IdentityError("invalid_input", "Invalid environment")

    def grants(self, scope: Scope) -> tuple[Grant, ...]:
        self._scope(scope)
        with self._lock:
            rows = self._db.execute(
                # Stored grants survive membership removal for audit and deliberate
                # restoration, but only currently admitted principals authorize.
                "SELECT g.id,g.tenant,g.kind,g.principal,g.role,g.revision FROM grants g "
                "WHERE g.org=? AND g.env=? AND g.deleted=0 AND EXISTS ("
                "SELECT 1 FROM memberships m WHERE m.org=g.org AND m.tenant=g.tenant "
                "AND m.kind=g.kind AND m.principal=g.principal) ORDER BY g.id",
                (scope.organization_id, scope.environment_id),
            ).fetchall()
            return tuple(Grant(row[0], scope, *row[1:]) for row in rows)

    def save_grant(
        self,
        grant: Grant,
        *,
        expected_revision: int,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> Grant:
        self._scope(grant.scope)
        self._identifier(grant.grant_id)
        self._identifier(grant.principal_id)
        if grant.principal_kind not in get_args(PrincipalKind) or grant.role not in get_args(Role):
            raise IdentityError("invalid_input", "Invalid principal kind or role")
        if type(expected_revision) is not int or expected_revision < 0:
            raise IdentityError("invalid_input", "Invalid expected revision")
        org, env = grant.scope.organization_id, grant.scope.environment_id
        with self._transaction():
            self._tenant(org, grant.directory_tenant_id)
            member = self._db.execute(
                "SELECT 1 FROM memberships WHERE org=? AND tenant=? AND kind=? AND principal=?",
                (org, grant.directory_tenant_id, grant.principal_kind, grant.principal_id),
            ).fetchone()
            if member is None:
                raise IdentityError("forbidden", "Principal is not an organization member")
            row = self._db.execute(
                "SELECT revision FROM grants WHERE org=? AND env=? AND id=?",
                (org, env, grant.grant_id),
            ).fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise IdentityError("conflict", "Grant revision changed")
            saved = replace(grant, revision=revision + 1)
            self._db.execute(
                "INSERT OR REPLACE INTO grants VALUES (?,?,?,?,?,?,?,?,0)",
                (
                    org,
                    env,
                    saved.grant_id,
                    saved.directory_tenant_id,
                    saved.principal_kind,
                    saved.principal_id,
                    saved.role,
                    saved.revision,
                ),
            )
            self._audit(
                org,
                "grant.save",
                saved.grant_id,
                actor_id,
                correlation_id,
                env=env,
                details=json.dumps(
                    {
                        "role": saved.role,
                        "principal_kind": saved.principal_kind,
                        "principal_id": saved.principal_id,
                        "revision": saved.revision,
                    },
                    sort_keys=True,
                ),
            )
            return saved

    def revoke_grant(
        self,
        scope: Scope,
        grant_id: str,
        *,
        expected_revision: int,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> None:
        self._scope(scope)
        with self._transaction():
            cursor = self._db.execute(
                "UPDATE grants SET deleted=1, revision=revision+1 WHERE org=? AND env=? AND id=? AND revision=? AND deleted=0",
                (scope.organization_id, scope.environment_id, grant_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise IdentityError("conflict", "Grant revision changed")
            self._audit(
                scope.organization_id,
                "grant.revoke",
                grant_id,
                actor_id,
                correlation_id,
                env=scope.environment_id,
                details=json.dumps({"revision": expected_revision + 1, "deleted": True}),
            )

    def provision_environments(
        self,
        organization_id: str,
        *,
        actor_id: str = "bootstrap",
        correlation_id: str = "bootstrap",
    ) -> tuple[Environment, ...]:
        with self._transaction():
            self._tenant(organization_id)
            digest = hashlib.sha256(organization_id.encode()).hexdigest()
            for env in ("dev", "uat", "prod"):
                route = f"nf-{digest}-{env}"
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO environments VALUES (?,?,?,?)",
                    (organization_id, env, route, f"{route}-worker"),
                )
                if cursor.rowcount:
                    self._audit(
                        organization_id,
                        "environment.provision",
                        env,
                        actor_id,
                        correlation_id,
                        env=env,
                    )
            return self.environments(organization_id)

    def environments(self, organization_id: str) -> tuple[Environment, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT env,namespace,queue FROM environments WHERE org=? ORDER BY CASE env WHEN 'dev' THEN 0 WHEN 'uat' THEN 1 ELSE 2 END",
                (organization_id,),
            ).fetchall()
            return tuple(
                Environment(
                    Scope(organization_id=organization_id, environment_id=row[0]), row[1], row[2]
                )
                for row in rows
            )

    def audit(self, organization_id: str) -> tuple[AuditEvent, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT sequence,org,action,resource,actor,correlation,timestamp,env,details FROM audit WHERE org=? ORDER BY sequence",
                (organization_id,),
            ).fetchall()
            return tuple(AuditEvent(*row) for row in rows)
