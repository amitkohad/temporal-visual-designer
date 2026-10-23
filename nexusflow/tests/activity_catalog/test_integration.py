"""Identity, concurrency and durable local-provider integration boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from nexusflow.modules.activity_catalog.authorization import IdentityCatalogAuthorization
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.service import IntakeService
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.tests.activity_catalog.test_intake import SCOPE, Authorization, source_bundle
from nexusflow.tests.identity.test_service import identity  # noqa: F401


def test_signed_identity_requires_publisher_and_checks_revocation(identity, tmp_path):  # noqa: F811
    identity_repo, scope, grant, keys, token = identity
    repo = SQLiteIntakeRepository(tmp_path / "intake.db")
    try:
        service = IntakeService(
            repo, IdentityCatalogAuthorization(IdentityService(identity_repo, keys), token())
        )
        archive, digest = source_bundle()
        with pytest.raises(IdentityError):
            service.import_bundle(scope, archive)
        identity_repo.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
        imported = service.import_bundle(scope, archive)
        assert imported.author_id == "pairwise-subject"
        identity_repo.revoke_grant(scope, grant.grant_id, expected_revision=2)
        with pytest.raises(IdentityError):
            service.download_import(scope, digest)
    finally:
        repo.close()


def test_separate_connections_concurrently_import_one_immutable_version(tmp_path):
    path = tmp_path / "intake.db"
    first, second = SQLiteIntakeRepository(path), SQLiteIntakeRepository(path)
    archive, digest = source_bundle()
    barrier = Barrier(2)

    def run(repo):
        barrier.wait(timeout=10)
        return IntakeService(repo, Authorization()).import_bundle(SCOPE, archive)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, repo) for repo in (first, second)]
            results = [future.result(timeout=15) for future in futures]
        assert results[0] == results[1]
        assert first.get(SCOPE, digest) == results[0]
    finally:
        first.close()
        second.close()
