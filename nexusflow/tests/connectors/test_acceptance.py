"""Independent metadata administration acceptance with real local persistence."""

import json
import warnings
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.service import ConnectionService
from nexusflow.modules.connectors.types import ConnectionError, ConnectionPage
from nexusflow.tests.connectors.test_definitions import definition

SCOPE = Scope(organization_id="acme", environment_id="dev")
REFERENCE = "credential-reference-sentinel"


class Authorization:
    def __init__(self):
        self.denied = False
        self.actor = "administrator-1"
        self.calls = []

    def authorize(self, scope, permission, resource):
        self.calls.append((scope, permission, resource))
        assert permission == "connection.manage" and resource == "connections"
        if self.denied:
            raise PermissionError("Access denied")
        return self.actor


@pytest.fixture
def connections(tmp_path):
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    auth = Authorization()
    service = ConnectionService(repository, auth)
    try:
        yield service, repository, auth
    finally:
        repository.close()


def test_metadata_reads_never_read_private_definitions_and_retries_preserve_author(connections):
    service, repository, auth = connections
    request = definition()
    first = service.put(SCOPE, "hr-api", "v1", request)
    private = repository.get(SCOPE, "hr-api", "v1")
    assert json.loads(private.definition_json)["secret_reference"] == REFERENCE
    assert private.metadata == first
    auth.actor = "administrator-2"
    assert service.put(SCOPE, "hr-api", "v1", request) == first
    assert first.actor_id == "administrator-1"
    repository.get = Mock(side_effect=AssertionError("Public reads must not load definition"))
    statements = []
    repository._db.set_trace_callback(statements.append)
    found = service.get(SCOPE, "hr-api", "v1")
    page = service.list(SCOPE, "hr-api")
    assert found == first and page.items == (first,)
    repository.get.assert_not_called()
    assert all(
        "definition_json" not in sql.lower()
        for sql in statements
        if sql.lstrip().lower().startswith("select")
    )
    for output in (found.model_dump_json(), page.model_dump_json()):
        assert REFERENCE not in output
        assert "api.example.com" not in output
        assert "allowed_origins" not in output
        assert "definition_json" not in output
    assert found.has_secret_reference is True


def test_reopen_preserves_revision_history_and_scope_boundaries(tmp_path):
    path = tmp_path / "connections.db"
    with_repo = SQLiteConnectionRepository(path)
    auth = Authorization()
    try:
        service = ConnectionService(with_repo, auth)
        first = service.put(SCOPE, "hr-api", "v1", definition())
        second = service.put(SCOPE, "hr-api", "v2", definition(secret_reference=None))
    finally:
        with_repo.close()
    repository = SQLiteConnectionRepository(path)
    try:
        service = ConnectionService(repository, auth)
        assert service.get(SCOPE, "hr-api", "v1") == first
        page = service.list(SCOPE, "hr-api", limit=1)
        assert page.items == (second,) and page.next_before_revision == 2
        assert service.list(SCOPE, "hr-api", before_revision=2).items == (first,)
        for scope in (
            SCOPE.model_copy(update={"environment_id": "prod"}),
            SCOPE.model_copy(update={"organization_id": "other"}),
        ):
            assert service.list(scope, "hr-api").items == ()
            with pytest.raises(ConnectionError) as error:
                service.get(scope, "hr-api", "v1")
            assert error.value.code == "not_found"
    finally:
        repository.close()


def test_revocation_precedes_every_retry_and_read(connections):
    service, repository, auth = connections
    request = definition()
    service.put(SCOPE, "hr-api", "v1", request)
    auth.denied = True
    repository.put = Mock(side_effect=AssertionError("Revoked caller reached provider"))
    repository.metadata = Mock(side_effect=AssertionError("Revoked caller reached provider"))
    repository.list = Mock(side_effect=AssertionError("Revoked caller reached provider"))
    for operation in (
        lambda: service.put(SCOPE, "hr-api", "v1", request),
        lambda: service.get(SCOPE, "hr-api", "v1"),
        lambda: service.list(SCOPE, "hr-api"),
    ):
        with pytest.raises(PermissionError):
            operation()
    repository.put.assert_not_called()
    repository.metadata.assert_not_called()
    repository.list.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "scope",
        "connection_id",
        "connection_revision",
        "created_at",
        "revision",
        "has_secret_reference",
    ],
)
def test_malformed_provider_metadata_is_not_returned(connections, field):
    service, repository, auth = connections
    first = service.put(SCOPE, "hr-api", "v1", definition())
    changes = {
        "scope": Scope(organization_id="other", environment_id="dev"),
        "connection_id": "foreign",
        "connection_revision": "foreign-revision",
        "created_at": "2026-10-29T10:00:00",
        "revision": True,
        "has_secret_reference": 1,
    }
    invalid = first.model_copy(update={field: changes[field]})
    fake = SimpleNamespace(metadata=Mock(return_value=invalid))
    checked = ConnectionService(fake, auth)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ConnectionError) as error:
            checked.get(SCOPE, "hr-api", "v1")
    assert error.value.code == "unavailable"
    assert "foreign" not in str(error.value)
    assert emitted == []
    repository.metadata(SCOPE, "hr-api", "v1")


@pytest.mark.parametrize(
    "field,value", [("definition_digest", "sha256:" + "a" * 64), ("has_secret_reference", False)]
)
def test_put_rejects_inconsistent_provider_definition_binding(connections, field, value):
    service, _, auth = connections
    first = service.put(SCOPE, "hr-api", "v1", definition())
    fake = SimpleNamespace(put=Mock(return_value=first.model_copy(update={field: value})))
    with pytest.raises(ConnectionError) as error:
        ConnectionService(fake, auth).put(SCOPE, "hr-api", "v1", definition())
    assert error.value.code == "unavailable"


@pytest.mark.parametrize("mutation", ["reversed", "duplicate", "foreign", "cursor", "before-bound"])
def test_inconsistent_provider_pages_fail_closed(connections, mutation):
    service, _, auth = connections
    first = service.put(SCOPE, "hr-api", "v1", definition())
    second = service.put(SCOPE, "hr-api", "v2", definition(secret_reference=None))
    page = ConnectionPage(items=(second, first), next_before_revision=None)
    before = None
    if mutation == "reversed":
        page = page.model_copy(update={"items": (first, second)})
    elif mutation == "duplicate":
        page = page.model_copy(update={"items": (second, second)})
    elif mutation == "foreign":
        page = page.model_copy(
            update={"items": (second.model_copy(update={"connection_id": "foreign"}), first)}
        )
    elif mutation == "cursor":
        page = page.model_copy(update={"next_before_revision": 2})
    else:
        before = 2
    checked = ConnectionService(SimpleNamespace(list=Mock(return_value=page)), auth)
    with pytest.raises(ConnectionError) as error:
        checked.list(SCOPE, "hr-api", limit=2, before_revision=before)
    assert error.value.code == "unavailable"


def test_sensitive_provider_errors_are_normalized(connections):
    _, _, auth = connections
    fake = SimpleNamespace(metadata=Mock(side_effect=OSError("credential-reference-sentinel")))
    with pytest.raises(ConnectionError) as error:
        ConnectionService(fake, auth).get(SCOPE, "hr-api", "v1")
    assert error.value.code == "unavailable"
    assert REFERENCE not in str(error.value)


def test_direct_repository_rejects_unchecked_reference_without_logging_or_writing(connections):
    _, repository, _ = connections
    unchecked = definition().model_copy(update={"secret_reference": 12345})
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(ConnectionError) as error:
            repository.put(SCOPE, "invalid-reference", "v1", unchecked, "administrator")
    assert error.value.code == "invalid_request"
    assert emitted == []
    assert repository.list(SCOPE, "invalid-reference").items == ()
