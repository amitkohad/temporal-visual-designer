"""Private evidence storage bounds, authorization and durable retry behavior."""

# ruff: noqa: F811, B023

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.package_promotion.attachments import (
    MAX_ATTACHMENT_BYTES,
    DeploymentAttachmentService,
)
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.tests.package_promotion.test_activation import SCOPE, request, system  # noqa: F401


@pytest.fixture
def evidence(system):
    activation, _, release, _, path = system
    activation.apply(SCOPE, request(release))
    return DeploymentAttachmentService(activation.repository, activation.authorization), path


def test_roundtrip_retry_metadata_only_and_restart(evidence):
    service, path = evidence
    before = service.repository.current(SCOPE, "hr")
    first = service.upload(SCOPE, "hr", "activate-a", "approval", b"private")
    assert first.actor_id == "author-1" and first.size == 7
    service.authorization.authorize = lambda *_: "another-author"
    assert service.upload(SCOPE, "hr", "activate-a", "approval", b"private") == first
    queries = []
    service.repository._db.set_trace_callback(queries.append)
    assert service.list(SCOPE, "hr", "activate-a") == (first,)
    assert all("content" not in sql.lower() for sql in queries)
    service.repository._db.set_trace_callback(None)
    assert service.repository.current(SCOPE, "hr") == before
    other = SQLiteActivationRepository(path / "activations.db")
    try:
        download = other.download_attachment(SCOPE, "hr", "activate-a", "approval")
        assert download.metadata == first and download.content == b"private"
        assert other._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        other.close()


@pytest.mark.parametrize(
    "content,name,code",
    [
        (b"", "Evidence", "invalid_request"),
        (bytearray(b"x"), "Evidence", "invalid_request"),
        ("x", "Evidence", "invalid_request"),
        (b"x", " ", "invalid_request"),
        (b"x", "x\n", "invalid_request"),
        (b"x", "x" * 129, "invalid_request"),
        (b"x", 1, "invalid_request"),
        (b"x" * (MAX_ATTACHMENT_BYTES + 1), "Evidence", "attachment_too_large"),
    ],
    ids=["empty", "bytearray", "text", "blank", "control", "long-label", "non-string", "oversize"],
)
def test_strict_upload_bounds(evidence, content, name, code):
    service, _ = evidence
    with pytest.raises(ActivationError) as error:
        service.upload(SCOPE, "hr", "activate-a", "id", content, name)
    assert error.value.code == code
    assert service.list(SCOPE, "hr", "activate-a") == ()


def test_maximum_size_conflicting_retry_and_quota(evidence):
    service, _ = evidence
    first = service.upload(SCOPE, "hr", "activate-a", "0", b"x" * MAX_ATTACHMENT_BYTES)
    for index in range(1, 10):
        service.upload(SCOPE, "hr", "activate-a", str(index), b"x")
    assert service.upload(SCOPE, "hr", "activate-a", "0", b"x" * MAX_ATTACHMENT_BYTES) == first
    for data, name in ((b"different", "Approval evidence"), (b"x" * MAX_ATTACHMENT_BYTES, "Other")):
        with pytest.raises(ActivationError) as error:
            service.upload(SCOPE, "hr", "activate-a", "0", data, name)
        assert error.value.code == "conflict"
    with pytest.raises(ActivationError) as error:
        service.upload(SCOPE, "hr", "activate-a", "overflow", b"x")
    assert error.value.code == "attachment_limit"


def test_scoped_existence_and_fresh_authorization(evidence):
    service, _ = evidence
    service.upload(SCOPE, "hr", "activate-a", "id", b"private")
    for scope, project, operation in (
        (Scope(organization_id="other", environment_id="dev"), "hr", "activate-a"),
        (Scope(organization_id="acme", environment_id="prod"), "hr", "activate-a"),
        (SCOPE, "other", "activate-a"),
        (SCOPE, "hr", "failed-operation"),
    ):
        for action in (
            lambda: service.list(scope, project, operation),
            lambda: service.download(scope, project, operation, "id"),
            lambda: service.upload(scope, project, operation, "id", b"x"),
        ):
            with pytest.raises(ActivationError) as error:
                action()
            assert error.value.code == "not_found"
    service.authorization.denied = True
    for action in (
        lambda: service.upload(SCOPE, "invalid/", "bad/", "bad/", b""),
        lambda: service.list(SCOPE, "invalid/", "bad/"),
        lambda: service.download(SCOPE, "invalid/", "bad/", "bad/"),
    ):
        with pytest.raises(PermissionError):
            action()
    assert [item[1] for item in service.authorization.calls[-3:]] == [
        "package.activate",
        "run.read",
        "run.read",
    ]


@pytest.mark.parametrize("identifier", ["bad/", "", 1, True])
def test_invalid_identifiers(evidence, identifier):
    service, _ = evidence
    with pytest.raises(ActivationError) as error:
        service.upload(SCOPE, "hr", "activate-a", identifier, b"x")
    assert error.value.code == "invalid_request"


def test_corrupt_download_and_storage_failures_are_safe(evidence):
    service, _ = evidence
    service.upload(SCOPE, "hr", "activate-a", "id", b"private")
    service.repository._db.execute("UPDATE deployment_attachments SET content=?", (b"changed",))
    service.repository._db.commit()
    with pytest.raises(ActivationError) as error:
        service.download(SCOPE, "hr", "activate-a", "id")
    assert error.value.code == "unavailable" and "changed" not in str(error.value)
    service.repository.close()
    for action in (
        lambda: service.list(SCOPE, "hr", "activate-a"),
        lambda: service.upload(SCOPE, "hr", "activate-a", "other", b"private"),
        lambda: service.download(SCOPE, "hr", "activate-a", "id"),
    ):
        with pytest.raises(ActivationError) as error:
            action()
        assert error.value.code == "unavailable"


def test_multiple_connections_serialize_quota(evidence):
    service, path = evidence
    repositories = [SQLiteActivationRepository(path / "activations.db") for _ in range(15)]

    def upload(item):
        index, repository = item
        try:
            return repository.put_attachment(
                SCOPE, "hr", "activate-a", str(index), b"x", "Evidence", "actor"
            )
        except ActivationError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(upload, enumerate(repositories)))
        assert results.count("attachment_limit") == 5
        assert len(service.list(SCOPE, "hr", "activate-a")) == 10
    finally:
        for repository in repositories:
            repository.close()


def test_additive_schema_for_existing_database(evidence):
    service, path = evidence
    service.repository._db.execute("DROP TABLE deployment_attachments")
    service.repository._db.commit()
    legacy = SQLiteActivationRepository(path / "activations.db")
    try:
        assert legacy.list_attachments(SCOPE, "hr", "activate-a") == ()
        with pytest.raises(sqlite3.IntegrityError):
            legacy._db.execute(
                "INSERT INTO deployment_attachments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("acme", "dev", "hr", "missing", "id", "Evidence", "bad", 1, "a", "now", b"x"),
            )
        legacy._db.rollback()
    finally:
        legacy.close()


def test_lost_commit_acknowledgement_is_retryable(evidence):
    service, _ = evidence
    original = service.repository.put_attachment

    def lost_ack(*args):
        original(*args)
        raise OSError("private driver detail")

    service.repository.put_attachment = lost_ack
    with pytest.raises(ActivationError) as error:
        service.upload(SCOPE, "hr", "activate-a", "id", b"private")
    assert error.value.code == "unavailable"
    service.repository.put_attachment = original
    assert (
        service.upload(SCOPE, "hr", "activate-a", "id", b"private")
        == service.list(SCOPE, "hr", "activate-a")[0]
    )
