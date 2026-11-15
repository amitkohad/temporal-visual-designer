"""Authorized destination preparation resolves secrets before DNS."""

import socket
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import DestinationError, HTTPDestinationRequest
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.secret_types import (
    HTTPAuthorizationCredential,
    SecretResolverError,
)
from nexusflow.modules.project_versioning.canonical import digest
from nexusflow.tests.connectors.test_definitions import definition

SCOPE = Scope(organization_id="organization", environment_id="dev")
PRIVATE = "credential-and-business-sentinel"
REQUEST = HTTPDestinationRequest(
    connection_id="api",
    connection_revision="v1",
    operation_id="operation",
    path="/path?q=" + PRIVATE,
)


class Authorization:
    def __init__(self, events):
        self.events = events
        self.denied = False
        self.actor = "worker"

    def authorize(self, scope, permission, resource_id):
        self.events.append("authorize")
        assert scope == SCOPE and permission == "worker.execute" and resource_id == "api"
        if self.denied:
            raise PermissionError(PRIVATE)
        return self.actor


class Resolver:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def resolve_ipv4(self, host):
        self.events.append("dns")
        assert host == "api.example.com"
        self.calls += 1
        return ("8.8.8.8", "1.1.1.1")


class Secrets:
    def __init__(self, events, credential=None, error=None):
        self.events = events
        self.credential = credential or HTTPAuthorizationCredential("Bearer", PRIVATE)
        self.error = error
        self.calls = 0

    def resolve(self, scope, secret_reference):
        self.events.append("resolve")
        self.calls += 1
        assert scope == SCOPE and secret_reference == "credential-reference-sentinel"
        if self.error is not None:
            raise self.error
        return self.credential


@pytest.fixture
def worker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only injected DNS; no actual network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    repository.put(SCOPE, "api", "v1", definition(), "admin")
    events = []
    auth, resolver, secrets = Authorization(events), Resolver(events), Secrets(events)
    original_get = repository.get

    def get(*args):
        events.append("get")
        return original_get(*args)

    monkeypatch.setattr(repository, "get", Mock(side_effect=get))
    try:
        yield (
            HTTPDestinationPlanner(repository, auth, resolver, secrets=secrets),
            repository,
            auth,
            resolver,
            secrets,
            events,
        )
    finally:
        repository.close()


def safe_error(error, code):
    assert error.code == code
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


@pytest.mark.asyncio
async def test_resolves_secret_before_dns_then_returns_credential(worker):
    planner, _, _, resolver, secrets, events = worker
    approved, credential = await planner.prepare_authorized(SCOPE, REQUEST)
    assert events == ["authorize", "get", "resolve", "dns"]
    assert resolver.calls == 1 and secrets.calls == 1
    assert credential.scheme == "Bearer" and credential.token == PRIVATE
    assert PRIVATE not in repr(approved)
    refreshed = planner.revalidate_authorized(SCOPE, REQUEST, approved, credential)
    assert refreshed.scheme == "Bearer"
    assert events == ["authorize", "get", "resolve", "dns", "authorize", "get", "resolve"]


@pytest.mark.asyncio
async def test_missing_secret_rejects_before_dns(worker):
    planner, _, _, resolver, secrets, events = worker
    secrets.error = SecretResolverError("credentials_required")
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_authorized(SCOPE, REQUEST)
    safe_error(caught.value, "credentials_required")
    assert resolver.calls == 0 and events == ["authorize", "get", "resolve"]


@pytest.mark.asyncio
async def test_authorization_denial_precedes_registry_secret_and_dns(worker):
    planner, repository, auth, resolver, secrets, events = worker
    auth.denied = True
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_authorized(SCOPE, REQUEST)
    safe_error(caught.value, "forbidden")
    assert events == ["authorize"]
    assert repository.get.call_count == resolver.calls == secrets.calls == 0


@pytest.mark.asyncio
async def test_credential_free_connection_still_rejects_configured_secrets(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only injected DNS; no actual network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    repository.put(SCOPE, "api", "v1", definition(), "admin")
    events = []
    auth, resolver = Authorization(events), Resolver(events)
    planner = HTTPDestinationPlanner(repository, auth, resolver)
    try:
        with pytest.raises(DestinationError) as caught:
            await planner.prepare_credential_free(SCOPE, REQUEST)
        safe_error(caught.value, "credentials_required")
        assert resolver.calls == 0
        with pytest.raises(DestinationError) as authorized:
            await planner.prepare_authorized(SCOPE, REQUEST)
        safe_error(authorized.value, "credentials_required")
        assert resolver.calls == 0
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_credential_free_path_unchanged_without_secret_reference(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only injected DNS; no actual network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    selected = definition(secret_reference=None)
    repository.put(SCOPE, "api", "v1", selected, "admin")
    events = []
    auth, resolver, secrets = Authorization(events), Resolver(events), Secrets(events)
    planner = HTTPDestinationPlanner(repository, auth, resolver, secrets=secrets)
    try:
        approved, credential = await planner.prepare_authorized(SCOPE, REQUEST)
        assert credential is None and secrets.calls == 0
        assert events == ["authorize", "dns"]
        assert planner.revalidate_authorized(SCOPE, REQUEST, approved, None) is None
        free = await planner.prepare_credential_free(SCOPE, REQUEST)
        assert free.definition_digest == digest(selected.model_dump(mode="json"))
        assert secrets.calls == 0
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_secret_revocation_after_dns_is_rejected_without_second_lookup(worker):
    planner, _, _, resolver, secrets, events = worker
    approved, credential = await planner.prepare_authorized(SCOPE, REQUEST)
    secrets.error = SecretResolverError("credentials_required")
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_authorized(SCOPE, REQUEST, approved, credential)
    safe_error(caught.value, "credentials_required")
    assert resolver.calls == 1
    assert events == ["authorize", "get", "resolve", "dns", "authorize", "get", "resolve"]
