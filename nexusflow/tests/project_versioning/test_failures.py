"""Boundary failures must leave durable state unchanged."""

import json

import pytest

from nexusflow.modules.project_versioning.types import ResolvedArtifact, VersioningError
from nexusflow.tests.project_versioning.test_service import (
    SCOPE,
    content,
    reference,
    system,  # noqa: F401
)


def test_publication_transaction_rolls_back_before_commit(system, monkeypatch):  # noqa: F811
    service, repo, _, _, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    original = repo.release

    def fail_after_insert(*args):
        raise OSError("injected storage failure")

    monkeypatch.setattr(repo, "release", fail_after_insert)
    with pytest.raises(OSError):
        service.publish(SCOPE, "hr", revision.number, "retry")
    monkeypatch.setattr(repo, "release", original)
    assert repo.published(SCOPE, "hr", "retry") is None
    release = service.publish(SCOPE, "hr", revision.number, "retry")
    assert release.source_revision == revision.number


@pytest.mark.parametrize("number", [True, 0, -1, 2**64])
def test_invalid_revision_numbers_cannot_alias_a_real_revision(system, number):  # noqa: F811
    service, _, _, _, _ = system
    service.create(SCOPE, "hr", "HR", content())
    with pytest.raises(VersioningError) as error:
        service.publish(SCOPE, "hr", number, "invalid")
    assert error.value.code == "invalid_request"


def test_nonfinite_json_rejected_without_writing(system):  # noqa: F811
    service, repo, _, _, _ = system
    payload = json.loads(content())
    payload["layout"] = {"x": float("nan")}
    with pytest.raises(VersioningError) as error:
        service.create(SCOPE, "hr", "HR", json.dumps(payload))
    assert error.value.code == "invalid_content"
    with pytest.raises(VersioningError):
        repo.project(SCOPE, "hr")


def test_catalog_outage_has_sanitized_error_and_no_release(system, monkeypatch):  # noqa: F811
    service, repo, _, _, catalog = system
    revision = service.create(SCOPE, "hr", "HR", content(refs=(reference("notify"),)))

    def unavailable(reference):
        raise OSError("credential-from-provider")

    monkeypatch.setattr(catalog, "resolve", unavailable)
    with pytest.raises(VersioningError) as error:
        service.publish(SCOPE, "hr", revision.number, "outage")
    assert error.value.code == "unavailable"
    assert "credential-from-provider" not in str(error.value)
    assert repo.published(SCOPE, "hr", "outage") is None


def test_conflicting_transitive_digest_is_not_published(system):  # noqa: F811
    service, repo, _, _, catalog = system
    one, two, conflicting = reference("one"), reference("two"), reference("two", "b")
    catalog.artifacts = {"one": ResolvedArtifact(one, (two,)), "two": ResolvedArtifact(two)}
    revision = service.create(SCOPE, "hr", "HR", content(refs=(one, conflicting)))
    with pytest.raises(VersioningError):
        service.publish(SCOPE, "hr", revision.number, "conflict")
    assert repo.published(SCOPE, "hr", "conflict") is None
