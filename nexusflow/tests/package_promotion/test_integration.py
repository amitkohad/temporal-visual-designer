"""Actual identity/catalog providers composed with activation and start resolution."""

import json
from dataclasses import replace

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.package_promotion.authorization import IdentityStartAuthorization
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.authorization import IdentityProjectAuthorization
from nexusflow.modules.project_versioning.types import VersioningError
from nexusflow.tests.activity_catalog.test_catalog_service import catalog, imported  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import SCOPE, content, request, system  # noqa: F401


def test_actual_catalog_capabilities_and_artifact_holds(system, catalog):  # noqa: F811
    service, publisher, a, _, _ = system
    activities, _, _, _, capabilities = catalog
    _, digest = imported(catalog)
    entry = activities.publish(SCOPE, digest)
    service.readiness.catalog = activities.repository
    service.readiness.capabilities = capabilities
    payload = content("activity")
    payload["activity_refs"] = [entry.reference.model_dump(mode="json")]
    revision = publisher.read(SCOPE, "hr")
    revision = publisher.save(SCOPE, "hr", revision.number, json.dumps(payload), "Pinned activity")
    release = publisher.publish(SCOPE, "hr", revision.number, "activity")
    capabilities.compatible = False
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(release))
    assert error.value.code in {"incompatible_release", "unavailable"}
    capabilities.compatible = True
    service.apply(SCOPE, request(release))
    withdrawn = activities.transition(SCOPE, entry.reference, "withdrawn", entry.generation)
    from nexusflow.modules.activity_catalog.types import CatalogError

    with pytest.raises(CatalogError):
        activities.delete(SCOPE, entry.reference)
    service.apply(SCOPE, request(a, 1, "back-to-a"))
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(release, 2, "rollback-activity", action="rollback"))
    assert error.value.code == "incompatible_release"
    assert service.current(SCOPE, "hr").release == a
    assert withdrawn.state == "withdrawn"


def test_missing_catalog_dependency_rejects_activation(system):  # noqa: F811
    service, publisher, _, _, _ = system
    payload = content("missing")
    payload["activity_refs"] = [
        {"activity_id": "missing", "version": "1.0.0", "artifact_digest": "sha256:" + "a" * 64}
    ]
    revision = publisher.read(SCOPE, "hr")
    revision = publisher.save(
        SCOPE, "hr", revision.number, json.dumps(payload), "Missing dependency"
    )
    release = publisher.publish(SCOPE, "hr", revision.number, "missing")
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(release))
    assert error.value.code == "incompatible_release"
    assert error.value.issues[0].code == "missing_activity"
    assert service.current(SCOPE, "hr") is None


def test_signed_identity_rechecks_grants_for_activation_and_start(system, identity):  # noqa: F811
    service, _, a, _, _ = system
    identity_repo, scope, grant, keys, token = identity
    authorization = IdentityProjectAuthorization(IdentityService(identity_repo, keys), token())
    service.authorization = authorization
    start_authorization = IdentityStartAuthorization(authorization)
    first = service.apply(scope, request(a))
    start_authorization.authorize(scope, "hr")
    assert first.actor_id == "pairwise-subject"
    identity_repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    with pytest.raises(IdentityError):
        service.apply(scope, request(a))
    with pytest.raises(IdentityError):
        start_authorization.authorize(scope, "hr")


def test_prod_author_denied_support_can_activate_without_evidence(system, identity):  # noqa: F811
    service, _, a, _, _ = system
    identity_repo, _, grant, keys, token = identity
    prod = Scope(organization_id="acme", environment_id="prod")
    prod_grant = replace(grant, scope=prod)
    identity_repo.save_grant(prod_grant, expected_revision=0)
    service.authorization = IdentityProjectAuthorization(
        IdentityService(identity_repo, keys), token()
    )
    with pytest.raises(IdentityError):
        service.apply(prod, request(a))
    assert service.repository.current(prod, "hr") is None
    # Seed a target-local release through the trusted repository only. This
    # fixture is not evidence that portable package import is implemented.
    revision = service.releases.create(prod, "hr", "HR", a.content_json, "Fixture", "support")
    target = service.releases.publish(
        prod,
        "hr",
        revision.number,
        "target",
        a.artifacts_json,
        a.content_digest,
        a.executable_digest,
        "support",
    )
    identity_repo.save_grant(replace(prod_grant, role="production_support"), expected_revision=1)
    accepted = service.apply(prod, request(target))
    assert accepted.release.scope == prod
    assert service.events(prod, "hr")[-1].outcome == "succeeded"
    IdentityStartAuthorization(service.authorization).authorize(prod, "hr")
    with pytest.raises(VersioningError):
        service.releases.delete_release(prod, "hr", target.release_id)
