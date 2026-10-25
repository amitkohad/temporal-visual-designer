"""Independent signed HTTP acceptance: staging never installs executable state."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.identity_environments.types import DirectoryBinding
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_project_package_acceptance import entries, export, release, repack
from nexusflow.tests.identity.test_service import CLIENT, TENANT, USER
from nexusflow.tests.package_promotion.test_activation import content


@pytest.fixture
def intake(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    archive = export(original, release(original, content())).content
    repository = SQLitePackageImportRepository(str(tmp_path / "imports.db"))
    with TestClient(
        create_app(replace(backend, package_imports=repository)), raise_server_exceptions=False
    ) as client:
        client.headers.update(original.headers)
        yield client, repository, backend, data, runtime, archive
    repository.close()


def stage(client, archive, operation="a", base=BASE):
    return client.put(
        base + "/package-imports/" + operation,
        content=archive,
        headers={"content-type": "application/zip"},
    )


def test_staging_is_immutable_scoped_and_preserves_first_archive(intake):
    client, _, backend, data, runtime, archive = intake
    identity_repo, scope, grant, _, _ = data
    uat = BASE.replace("/dev", "/uat")
    identity_repo.save_grant(
        replace(grant, scope=scope.model_copy(update={"environment_id": "uat"})),
        expected_revision=0,
    )
    result = stage(client, archive, base=uat)
    assert result.status_code == 201, result.text
    metadata = result.json()
    assert metadata["state"] == "staged"
    assert metadata["actor_id"] == "pairwise-subject"
    assert metadata["revision"] == 1
    assert stage(client, repack(entries(archive)), base=uat).json() == metadata
    path = uat + "/package-imports/a"
    assert client.get(path).json() == metadata
    download = client.get(path + "/archive")
    assert download.content == archive
    assert download.headers["x-package-digest"] == metadata["package_digest"]
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert (
        download.headers["content-disposition"] == 'attachment; filename="nexus-flow-project.zip"'
    )
    assert client.get(path + "/manifest").json()["package_digest"] == metadata["package_digest"]
    assert client.get(uat + "/projects").json() == []
    assert client.get(uat + "/projects/hr/activation").json()["release_id"] is None
    assert backend.projects.project(scope, "hr").head_revision == 1
    assert not runtime.plans
    assert client.get(BASE + "/package-imports/a").status_code == 404
    identity_repo.revoke_grant(
        scope.model_copy(update={"environment_id": "uat"}), grant.grant_id, expected_revision=1
    )
    for suffix in ("", "/archive", "/manifest"):
        assert client.get(path + suffix).status_code == 403
    assert stage(client, archive, base=uat).status_code == 403


@pytest.mark.parametrize("mutation", ["tampered", "traversal", "duplicate-json", "invalid"])
def test_invalid_archive_never_persists_partial_receipt(intake, mutation):
    client, _, _, _, _, archive = intake
    files = entries(archive)
    if mutation == "tampered":
        files["project.json"] += b" "
    elif mutation == "traversal":
        files["../private-sensitive"] = b"secret"
    elif mutation == "duplicate-json":
        files["manifest.json"] = files["manifest.json"].replace(
            b"{", b'{"project_id":"private-sensitive",', 1
        )
    failed = stage(client, b"private-sensitive" if mutation == "invalid" else repack(files))
    assert failed.status_code == 422, failed.text
    assert "private-sensitive" not in failed.text
    assert client.get(BASE + "/package-imports").json()["items"] == []
    assert client.get(BASE + "/package-imports/a").status_code == 404


def test_pagination_and_concurrent_lost_ack_retries(intake):
    client, _, _, _, _, archive = intake
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: stage(client, archive), range(8)))
    assert all(r.status_code == 201 for r in results)
    assert all(r.json() == results[0].json() for r in results)
    assert stage(client, archive, "b").status_code == 201
    first = client.get(BASE + "/package-imports?limit=1").json()
    assert first["items"][0]["operation_id"] == "b"
    assert first["next_before_revision"] == 2
    assert stage(client, archive, "c").status_code == 201
    second = client.get(BASE + "/package-imports?limit=1&before_revision=2").json()
    assert second["items"][0]["operation_id"] == "a"
    assert second["next_before_revision"] is None


def test_unconfigured_storage_authorizes_before_unavailable(api):  # noqa: F811
    client, _, data, _ = api
    assert client.get(BASE + "/package-imports").status_code == 503
    repository, scope, grant, _, _ = data
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(BASE + "/package-imports").status_code == 403
    assert stage(client, b"bad").status_code == 403


def test_import_does_not_consult_unrelated_providers_and_prod_needs_support(intake):
    client, _, backend, data, _, archive = intake
    repository, scope, grant, _, token = data

    class Unavailable:
        def __getattr__(self, name):
            raise AssertionError("Unrelated provider consulted: " + name)

    # Explicitly keep the real intake store while removing all execution/readiness ports.
    with TestClient(
        create_app(
            replace(
                backend,
                package_imports=intake[1],
                catalog=Unavailable(),
                intake=Unavailable(),
                configurations=Unavailable(),
                runtime=Unavailable(),
            )
        ),
        raise_server_exceptions=False,
    ) as other:
        other.headers["authorization"] = "Bearer " + token()
        assert stage(other, archive).status_code == 201
    prod_scope = scope.model_copy(update={"environment_id": "prod"})
    repository.save_grant(replace(grant, scope=prod_scope), expected_revision=0)
    prod = BASE.replace("/dev", "/prod")
    assert stage(client, archive, base=prod).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod_scope, role="production_support"), expected_revision=1
    )
    assert stage(client, archive, base=prod).status_code == 201


def test_changed_package_conflicts_and_cross_organization_is_denied(intake):
    client, _, _, data, _, archive = intake
    assert stage(client, archive).status_code == 201
    assert (
        client.put(
            BASE + "/projects/hr",
            json={"expected_revision": 1, "summary": "Changed", "content": content("B")},
        ).status_code
        == 200
    )
    published = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 2, "idempotency_key": "changed"}
    )
    assert published.status_code == 201, published.text
    changed = export(client, published.json()["release_id"]).content
    assert stage(client, changed).status_code == 409
    assert client.get(BASE + "/package-imports/a/archive").content == archive
    repository, scope, grant, _, _ = data
    repository.register_binding(
        DirectoryBinding("other", TENANT, "nexus-api", allowed_user_clients=(CLIENT,))
    )
    repository.provision_environments("other")
    repository.add_membership("other", TENANT, "user", USER)
    other_scope = scope.model_copy(update={"organization_id": "other"})
    repository.save_grant(replace(grant, scope=other_scope), expected_revision=0)
    other = BASE.replace("/acme/", "/other/")
    assert stage(client, archive, base=other).status_code == 403
    assert client.get(other + "/package-imports").json()["items"] == []


def test_concurrent_quota_and_corrupt_archive_fail_safely(intake, tmp_path):
    original, _, backend, data, _, archive = intake
    repository = SQLitePackageImportRepository(
        str(tmp_path / "quota.db"), maximum_imports_per_scope=2
    )
    with TestClient(
        create_app(replace(backend, package_imports=repository)), raise_server_exceptions=False
    ) as client:
        client.headers.update(original.headers)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda n: stage(client, archive, "op-" + str(n)), range(8)))
        assert sum(r.status_code == 201 for r in results) == 2
        assert all(r.status_code in (201, 409) for r in results)
        items = client.get(BASE + "/package-imports").json()["items"]
        assert len(items) == 2
        operation = items[0]["operation_id"]
        assert stage(client, archive, operation).status_code == 201
        repository._db.execute(
            "UPDATE package_imports SET archive=? WHERE operation_id=?",
            (b"private-corruption", operation),
        )
        repository._db.commit()
        response = client.get(BASE + "/package-imports/" + operation + "/archive")
        assert response.status_code == 503, response.text
        assert "private-corruption" not in response.text
        assert client.get(BASE + "/package-imports/" + operation).status_code == 200
    repository.close()
