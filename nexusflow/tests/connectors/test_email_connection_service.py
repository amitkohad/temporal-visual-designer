"""Fresh policy and defensive metadata boundaries; no email provider is invoked."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_connection_service import EmailConnectionService
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionPage,
    EmailConnectionRepository,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.types import ConnectionError
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import digest

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-show-private-email-value"


def request():
    return EmailConnectionWrite(
        smtp_host="PRIVATE-MAIL.example.com",
        sender="Sender@EXAMPLE.TEST",
        allowed_recipient_domains=("example.test",),
        secret_reference=PRIVATE,
    )


def metadata(**changes):
    value = EmailConnectionMetadata(
        scope=SCOPE,
        connection_id="mail",
        connection_revision="v1",
        revision=1,
        definition_digest=digest(request().model_dump(mode="json")),
        has_secret_reference=True,
        actor_id="original-admin",
        created_at=datetime(2026, 11, 5, tzinfo=UTC).isoformat(),
    )
    return value.model_copy(update=changes)


class Authorization:
    def __init__(self):
        self.calls = []
        self.denied = False
        self.failure = None

    def authorize(self, scope, permission, resource_id):
        self.calls.append((scope, permission, resource_id))
        if self.failure is not None:
            raise self.failure
        if self.denied:
            raise PermissionError(PRIVATE)
        return "administrator"


@pytest.fixture
def ports():
    repository = Mock(spec=EmailConnectionRepository)
    repository.put.return_value = metadata()
    repository.metadata.return_value = metadata()
    repository.list.return_value = EmailConnectionPage(
        items=(metadata(),), next_before_revision=None
    )
    auth = Authorization()
    return EmailConnectionService(repository, auth), repository, auth


def invoke(service, operation):
    if operation == "put":
        return service.put(SCOPE, "mail", "v1", request())
    if operation == "get":
        return service.get(SCOPE, "mail", "v1")
    return service.list(SCOPE, "mail")


def failure(call, code="unavailable"):
    with pytest.raises(ConnectionError) as caught:
        call()
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("operation", ["put", "get", "list"])
def test_fresh_permission_before_and_after_provider(ports, operation):
    service, repository, auth = ports
    result = invoke(service, operation)
    assert result is not None
    assert auth.calls == [(SCOPE, "connection.manage", "connections")] * 2
    repository.get.assert_not_called()


@pytest.mark.parametrize("operation", ["put", "get", "list"])
def test_denial_precedes_every_provider(ports, operation):
    service, repository, auth = ports
    auth.denied = True
    failure(lambda: invoke(service, operation), "forbidden")
    assert repository.mock_calls == []


@pytest.mark.parametrize("operation", ["put", "get", "list"])
def test_revocation_after_provider_blocks_metadata_release(ports, operation):
    service, repository, auth = ports
    target = repository.metadata if operation == "get" else getattr(repository, operation)
    result = target.return_value

    def revoke(*args, **kwargs):
        auth.denied = True
        return result

    target.side_effect = revoke
    failure(lambda: invoke(service, operation), "forbidden")
    assert len(auth.calls) == 2


@pytest.mark.parametrize("code", ["unauthenticated", "forbidden"])
def test_trusted_identity_failure_classification_is_preserved(ports, code):
    service, repository, auth = ports
    error = IdentityError(code, PRIVATE)
    auth.failure = error
    with pytest.raises(IdentityError) as caught:
        service.get(SCOPE, "mail", "v1")
    assert caught.value is error
    assert repository.mock_calls == []


@pytest.mark.parametrize("operation", ["put", "get", "list"])
@pytest.mark.parametrize(
    "error", [OSError(PRIVATE), RuntimeError(PRIVATE), PermissionError(PRIVATE)]
)
def test_provider_exceptions_are_private_unavailable(ports, operation, error):
    service, repository, _ = ports
    target = repository.metadata if operation == "get" else getattr(repository, operation)
    target.side_effect = error
    failure(lambda: invoke(service, operation))


@pytest.mark.parametrize(
    "code", ["not_found", "conflict", "limit_exceeded", "invalid_request", "unavailable"]
)
def test_known_storage_codes_are_preserved_privately(ports, code):
    service, repository, _ = ports
    repository.metadata.side_effect = ConnectionError(code, PRIVATE)
    failure(lambda: service.get(SCOPE, "mail", "v1"), code)


@pytest.mark.parametrize("code", [None, [], "unknown", "forbidden", "unauthenticated"])
def test_unknown_storage_error_codes_become_unavailable(ports, code):
    service, repository, _ = ports
    repository.metadata.side_effect = ConnectionError(code, PRIVATE)
    failure(lambda: service.get(SCOPE, "mail", "v1"))


def test_hostile_error_code_is_never_compared_or_hashed(ports):
    service, repository, _ = ports

    class Hostile:
        def __eq__(self, other):
            raise AssertionError(PRIVATE)

        def __hash__(self):
            raise AssertionError(PRIVATE)

    repository.metadata.side_effect = ConnectionError(Hostile(), PRIVATE)
    failure(lambda: service.get(SCOPE, "mail", "v1"))


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": SCOPE.model_copy(update={"organization_id": "foreign"})},
        {"connection_id": "other"},
        {"connection_revision": "other"},
        {"revision": True},
        {"connector_type": "http"},
        {"transport": "smtp"},
        {"has_secret_reference": "yes"},
        {"created_at": "2026-11-05T00:00:00"},
        {"created_at": "2026-11-05T00:00:00Z"},
        {"created_at": "2026-11-05T01:00:00+01:00"},
        {"actor_id": PRIVATE + "/invalid"},
    ],
)
def test_malformed_metadata_is_not_released(ports, changes):
    service, repository, _ = ports
    repository.metadata.return_value = metadata(**changes)
    failure(lambda: service.get(SCOPE, "mail", "v1"))


@pytest.mark.parametrize(
    "changes", [{"definition_digest": "sha256:" + "a" * 64}, {"has_secret_reference": False}]
)
def test_put_receipt_must_match_exact_private_definition(ports, changes):
    service, repository, _ = ports
    repository.put.return_value = metadata(**changes)
    failure(lambda: service.put(SCOPE, "mail", "v1", request()))


@pytest.mark.parametrize(
    "changes",
    [
        {"smtp_host": "https://" + PRIVATE + ".test"},
        {"port": True},
        {"sender": PRIVATE + "\r\n@example.test"},
        {"allowed_recipient_domains": ["*.example.test"]},
    ],
)
def test_constructed_inputs_are_revalidated_before_provider(ports, changes):
    service, repository, auth = ports
    value = request().model_copy(update=changes)
    failure(lambda: service.put(SCOPE, "mail", "v1", value), "invalid_request")
    repository.put.assert_not_called()
    assert len(auth.calls) == 1


@pytest.mark.parametrize(
    "limit,before", [(True, None), (0, None), (51, None), (1, True), (1, 0), (1, 2**63)]
)
def test_bad_pagination_never_reaches_provider(ports, limit, before):
    service, repository, _ = ports
    failure(
        lambda: service.list(SCOPE, "mail", limit=limit, before_revision=before), "invalid_request"
    )
    repository.list.assert_not_called()


@pytest.mark.parametrize(
    "items,cursor",
    [
        ((metadata(), metadata()), None),
        ((metadata(revision=2), metadata(connection_revision="v2", revision=3)), None),
        ((metadata(),), 2),
        ((), 1),
        ((metadata(revision=5),), None),
    ],
)
def test_provider_history_cannot_break_order_cursor_or_requested_bound(ports, items, cursor):
    service, repository, _ = ports
    repository.list.return_value = EmailConnectionPage.model_construct(
        items=items, next_before_revision=cursor
    )
    failure(lambda: service.list(SCOPE, "mail", limit=2, before_revision=4))


def test_metadata_and_input_snapshots_are_independent(ports):
    service, repository, _ = ports
    source = metadata()
    repository.metadata.return_value = source
    result = service.get(SCOPE, "mail", "v1")
    assert result is not source and result.scope is not source.scope
    object.__setattr__(source.scope, "organization_id", "mutated")
    try:
        assert result.scope.organization_id == "acme"
    finally:
        object.__setattr__(source.scope, "organization_id", "acme")
