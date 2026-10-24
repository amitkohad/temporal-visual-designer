"""Signed token → real SQLite metadata → scoped server context acceptance."""

import json
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.service import AdmissionLimiter, IdentityService
from nexusflow.modules.identity_environments.types import DirectoryBinding, Grant, IdentityError

TENANT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
GROUP = "44444444-4444-4444-8444-444444444444"


class Keys:
    def __init__(self, private_key):
        self.key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        self.key.update(kid="local-test", alg="RS256", use="sig")
        self.calls = 0

    def jwks(self, directory_tenant_id):
        assert directory_tenant_id == TENANT
        self.calls += 1
        return {"keys": [self.key]}


class Groups:
    def __init__(self):
        self.values = frozenset({GROUP})
        self.available = True

    def groups(self, directory_tenant_id, object_id):
        assert (directory_tenant_id, object_id) == (TENANT, USER)
        if not self.available:
            raise OSError("sensitive-provider-error")
        return self.values


@pytest.fixture
def identity(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    repo = SQLiteIdentityRepository(tmp_path / "identity.db")
    binding = DirectoryBinding("acme", TENANT, "nexus-api", allowed_user_clients=(CLIENT,))
    repo.register_binding(binding)
    repo.provision_environments("acme")
    repo.add_membership("acme", TENANT, "user", USER)
    scope = Scope(organization_id="acme", environment_id="dev")
    grant = Grant("author", scope, TENANT, "user", USER, "business_author")
    repo.save_grant(grant, expected_revision=0)
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": "nexus-api",
        "tid": TENANT,
        "sub": "pairwise-subject",
        "oid": USER,
        "azp": CLIENT,
        "ver": "2.0",
        "scp": "access_as_user",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }
    keys = Keys(private)

    def token(_omit=(), **updates):
        payload = {**claims, **updates}
        for name in _omit:
            payload.pop(name, None)
        return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "local-test"})

    yield repo, scope, grant, keys, token
    repo.close()


def authorize(service, token, scope, permission="project.write"):
    return service.authorize(
        token, scope, permission, request_id="request-1", correlation_id="trace-1"
    )


def test_context_uses_signed_identity_and_server_grants(identity):
    repo, scope, _, keys, token = identity
    context = authorize(IdentityService(repo, keys), token(roles=["production_support"]), scope)
    assert context.actor.subject == "pairwise-subject"
    assert context.scope == scope
    assert "project.write" in context.resolved_permissions
    assert "activity.publish" not in context.resolved_permissions
    assert keys.calls == 1
    assert all("token" not in str(event).lower() for event in repo.audit("acme"))
    assert all(
        datetime.fromisoformat(event.timestamp).tzinfo is not None for event in repo.audit("acme")
    )


def test_task_identity_returns_verified_object_id_and_rechecks_policy(identity):
    repo, scope, grant, keys, token = identity
    service = IdentityService(repo, keys)
    access_token = token()

    def resolve():
        return service.authorize_user_object_id(
            access_token,
            scope,
            "project.read",
            request_id="task-request",
            correlation_id="task-trace",
        )

    assert resolve() == USER
    repo.revoke_grant(scope, grant.grant_id, expected_revision=grant.revision)
    with pytest.raises(IdentityError):
        resolve()


def test_task_identity_rejects_an_otherwise_authorized_workload(identity):
    repo, _, _, keys, token = identity
    scope = Scope(organization_id="worker-org", environment_id="dev")
    repo.register_binding(
        DirectoryBinding("worker-org", TENANT, "nexus-api", allowed_workload_clients=(CLIENT,))
    )
    repo.provision_environments("worker-org")
    repo.add_membership("worker-org", TENANT, "workload", USER)
    repo.save_grant(
        Grant("worker-role", scope, TENANT, "workload", USER, "worker"), expected_revision=0
    )
    service = IdentityService(repo, keys)
    access_token = token(_omit=("scp",), idtyp="app", roles=["NexusFlow.Worker"])
    assert authorize(service, access_token, scope, "worker.execute").scope == scope
    with pytest.raises(IdentityError) as failure:
        service.authorize_user_object_id(
            access_token, scope, "worker.execute", request_id="request", correlation_id="trace"
        )
    assert failure.value.code == "forbidden"


def test_production_permission_cannot_be_injected_by_token(identity):
    repo, scope, _, keys, token = identity
    service = IdentityService(repo, keys)
    with pytest.raises(IdentityError) as error:
        authorize(
            service,
            token(roles=["production_support"]),
            scope.model_copy(update={"environment_id": "prod"}),
            "package.activate",
        )
    assert error.value.code == "forbidden"


def test_role_revocation_applies_on_next_request(identity):
    repo, scope, grant, keys, token = identity
    service = IdentityService(repo, keys)
    access_token = token()
    authorize(service, access_token, scope)
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    with pytest.raises(IdentityError, match="Access denied"):
        authorize(service, access_token, scope)
    revoked = next(event for event in repo.audit("acme") if event.action == "grant.revoke")
    assert revoked.environment_id == "dev"
    assert json.loads(revoked.details_json) == {"revision": 2, "deleted": True}


def test_directory_group_removal_and_outage_fail_closed(identity):
    repo, scope, grant, keys, token = identity
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    repo.add_membership("acme", TENANT, "group", GROUP)
    repo.save_grant(
        Grant("group-author", scope, TENANT, "group", GROUP, "business_author"), expected_revision=0
    )
    groups = Groups()
    service = IdentityService(repo, keys, groups=groups)
    access_token = token(groups=[GROUP])
    authorize(service, access_token, scope)
    groups.values = frozenset()
    with pytest.raises(IdentityError) as error:
        authorize(service, access_token, scope)
    assert error.value.code == "forbidden"
    groups.available = False
    with pytest.raises(IdentityError) as error:
        authorize(service, access_token, scope)
    assert error.value.code == "unavailable"
    assert "sensitive-provider-error" not in str(error.value)


def test_placement_is_derived_from_scoped_registry(identity):
    repo, scope, _, keys, token = identity
    route = IdentityService(repo, keys).placement(
        token(), scope, request_id="request-1", correlation_id="trace-1"
    )
    assert route in repo.environments("acme")
    assert route.scope == scope
    assert route.temporal_namespace
    assert route.task_queue


def test_same_directory_cannot_cross_another_organization(identity):
    repo, _, _, keys, token = identity
    repo.register_binding(
        DirectoryBinding("other", TENANT, "nexus-api", allowed_user_clients=(CLIENT,))
    )
    repo.provision_environments("other")
    service = IdentityService(repo, keys)
    other = Scope(organization_id="other", environment_id="dev")
    with pytest.raises(IdentityError, match="Access denied"):
        authorize(service, token(), other)


def test_workload_token_is_limited_to_explicit_worker_scope(identity):
    repo, _, _, keys, token = identity
    # Use another organization to configure application credentials independently.
    repo.register_binding(
        DirectoryBinding("workers", TENANT, "nexus-api", allowed_workload_clients=(CLIENT,))
    )
    repo.provision_environments("workers")
    worker_scope = Scope(organization_id="workers", environment_id="dev")
    repo.add_membership("workers", TENANT, "workload", USER)
    repo.save_grant(
        Grant("worker", worker_scope, TENANT, "workload", USER, "worker"), expected_revision=0
    )
    app_token = token(idtyp="app", roles=["NexusFlow.Worker"], scp=None)
    # Application tokens carrying scp at all are rejected, even when null.
    with pytest.raises(IdentityError) as error:
        authorize(IdentityService(repo, keys), app_token, worker_scope, "worker.execute")
    assert error.value.code == "unauthenticated"
    service = IdentityService(repo, keys)
    app_token = token(_omit=("scp",), idtyp="app", roles=["NexusFlow.Worker"])
    context = authorize(service, app_token, worker_scope, "worker.execute")
    assert context.resolved_permissions == ("run.read", "worker.execute")
    assert (
        service.placement(
            app_token, worker_scope, request_id="worker", correlation_id="worker"
        ).scope
        == worker_scope
    )
    with pytest.raises(IdentityError) as error:
        authorize(
            service,
            app_token,
            worker_scope.model_copy(update={"environment_id": "prod"}),
            "worker.execute",
        )
    assert error.value.code == "forbidden"


def test_limiter_stops_expensive_verification_and_resets(identity):
    repo, scope, _, keys, token = identity
    clock = [0.0]
    limiter = AdmissionLimiter(maximum=1, window_seconds=60, clock=lambda: clock[0])
    service = IdentityService(repo, keys, limiter=limiter)
    access_token = token()
    authorize(service, access_token, scope)
    with pytest.raises(IdentityError) as error:
        authorize(service, access_token, scope)
    assert error.value.code == "unavailable"
    assert keys.calls == 1
    clock[0] = 60.0
    authorize(service, access_token, scope)
    assert keys.calls == 2


def test_limiter_memory_is_bounded():
    clock = [0.0]
    limiter = AdmissionLimiter(maximum_buckets=1, clock=lambda: clock[0])
    limiter.admit("one")
    with pytest.raises(IdentityError):
        limiter.admit("two")
    clock[0] = 61.0
    limiter.admit("two")


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum": 0},
        {"window_seconds": 0},
        {"maximum_buckets": 0},
        {"window_seconds": float("nan")},
        {"window_seconds": float("inf")},
        {"maximum": True},
        {"maximum_buckets": True},
    ],
)
def test_limiter_requires_positive_limits(changes):
    with pytest.raises(ValueError):
        AdmissionLimiter(**changes)


@pytest.mark.parametrize("operation", ["authorize", "placement"])
def test_malformed_token_does_not_reveal_organization_binding(identity, operation):
    repo, scope, _, keys, _ = identity
    service = IdentityService(repo, keys)
    failures = []
    for target in (scope, Scope(organization_id="unknown", environment_id="dev")):
        kwargs = {"request_id": "request-1", "correlation_id": "trace-1"}
        if operation == "authorize":
            kwargs["permission"] = "project.read"
        with pytest.raises(IdentityError) as error:
            getattr(service, operation)("malformed-token", target, **kwargs)
        failures.append((error.value.code, str(error.value)))
    assert failures == [("unauthenticated", "Invalid access token")] * 2
