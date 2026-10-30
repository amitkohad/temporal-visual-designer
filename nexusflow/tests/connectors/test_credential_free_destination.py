"""Credential-free initial-hop preparation/revalidation against actual SQLite."""

import asyncio
import socket
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import (
    ApprovedHTTPDestination,
    DestinationError,
    HTTPDestinationRequest,
)
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.types import ConnectionRecord
from nexusflow.modules.project_versioning.canonical import canonical, digest
from nexusflow.tests.connectors.test_definitions import definition

SCOPE = Scope(organization_id="organization", environment_id="dev")
OTHER = Scope(organization_id="organization", environment_id="uat")
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


@pytest.fixture
def worker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only injected DNS; no actual network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    repository.put(SCOPE, "api", "v1", definition(secret_reference=None), "admin")
    events = []
    auth, resolver = Authorization(events), Resolver(events)
    original_get = repository.get

    def get(*args):
        events.append("get")
        return original_get(*args)

    monkeypatch.setattr(repository, "get", Mock(side_effect=get))
    try:
        yield HTTPDestinationPlanner(repository, auth, resolver), repository, auth, resolver, events
    finally:
        repository.close()


def private_record(repository, monkeypatch, selected):
    original = repository.get(SCOPE, "api", "v1")
    metadata = original.metadata.model_copy(
        update={
            "definition_digest": digest(selected.model_dump(mode="json")),
            "has_secret_reference": selected.secret_reference is not None,
        }
    )
    monkeypatch.setattr(
        repository,
        "get",
        Mock(return_value=ConnectionRecord(metadata, canonical(selected.model_dump(mode="json")))),
    )


def safe_error(error, code):
    assert error.code == code
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


@pytest.mark.asyncio
async def test_prepares_then_revalidates_without_a_second_dns_lookup(worker):
    planner, repository, _, resolver, events = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    assert events == ["authorize", "get", "dns"]
    assert planner.revalidate_credential_free(SCOPE, REQUEST, approved) is None
    assert events == ["authorize", "get", "dns", "authorize", "get"]
    assert approved.scope == SCOPE and approved.operation_id == REQUEST.operation_id
    assert approved.ipv4_addresses == ("1.1.1.1", "8.8.8.8")
    assert (
        approved.definition_digest == repository.get(SCOPE, "api", "v1").metadata.definition_digest
    )
    assert resolver.calls == 1 and PRIVATE not in repr(approved)


@pytest.mark.asyncio
async def test_secret_reference_is_rejected_before_dns(worker, monkeypatch):
    planner, repository, _, resolver, _ = worker
    private_record(repository, monkeypatch, definition(secret_reference=PRIVATE))
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_credential_free(SCOPE, REQUEST)
    safe_error(caught.value, "credentials_required")
    assert resolver.calls == 0


@pytest.mark.asyncio
async def test_authorization_denial_precedes_registry_and_dns(worker):
    planner, repository, auth, resolver, events = worker
    auth.denied = True
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_credential_free(SCOPE, REQUEST)
    safe_error(caught.value, "forbidden")
    assert events == ["authorize"] and repository.get.call_count == resolver.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, False, 4, "", "invalid actor"])
async def test_malformed_actor_never_reaches_registry_or_dns(actor, worker):
    planner, repository, auth, resolver, _ = worker
    auth.actor = actor
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_credential_free(SCOPE, REQUEST)
    safe_error(caught.value, "unavailable")
    assert repository.get.call_count == resolver.calls == 0


@pytest.mark.asyncio
async def test_revocation_after_dns_is_rejected_before_rereading_registry(worker):
    planner, repository, auth, resolver, events = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    auth.denied = True
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, approved)
    safe_error(caught.value, "forbidden")
    assert events == ["authorize", "get", "dns", "authorize"]
    assert repository.get.call_count == resolver.calls == 1


@pytest.mark.asyncio
async def test_secret_appearing_after_dns_cannot_be_silently_ignored(worker, monkeypatch):
    planner, repository, _, resolver, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    private_record(repository, monkeypatch, definition(secret_reference=PRIVATE))
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, approved)
    safe_error(caught.value, "credentials_required")
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_coherent_changed_definition_digest_is_rejected(worker, monkeypatch):
    planner, repository, _, resolver, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    private_record(
        repository,
        monkeypatch,
        definition(
            secret_reference=None,
            allowed_origins=["https://api.example.com", "https://other.example.com"],
        ),
    )
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, approved)
    safe_error(caught.value, "invalid_request")
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_corrupt_definition_during_revalidation_is_normalized(worker, monkeypatch):
    planner, repository, _, resolver, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    record = repository.get(SCOPE, "api", "v1")
    monkeypatch.setattr(
        repository, "get", Mock(return_value=replace(record, definition_json=PRIVATE))
    )
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, approved)
    safe_error(caught.value, "unavailable")
    assert resolver.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"scope": OTHER},
        {"scope": None},
        {"connection_id": "different"},
        {"connection_revision": "v2"},
        {"operation_id": "different"},
        {"definition_digest": "sha256:" + "0" * 64},
        {"url": "https://api.example.com/different"},
        {"origin": "https://other.example.com"},
        {"host": "other.example.com"},
        {"port": 8443},
        {"port": True},
        {"redirect_count": 1},
        {"redirect_count": False},
        {"ipv4_addresses": ("127.0.0.1",)},
        {"ipv4_addresses": ("8.8.8.8", "127.0.0.1")},
        {"ipv4_addresses": ("8.8.8.8", "1.1.1.1")},
        {"ipv4_addresses": ("8.8.8.8", "8.8.8.8")},
        {"ipv4_addresses": ["8.8.8.8"]},
        {"ipv4_addresses": ()},
        {"ipv4_addresses": ("168.63.129.16",)},
    ],
)
async def test_malformed_or_cross_bound_approval_is_rejected(changes, worker):
    planner, _, _, resolver, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, replace(approved, **changes))
    safe_error(caught.value, "invalid_request")
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_different_request_path_cannot_reuse_an_approval(worker):
    planner, _, _, resolver, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(
            SCOPE, REQUEST.model_copy(update={"path": "/other"}), approved
        )
    safe_error(caught.value, "invalid_request")
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_approval_subclass_is_not_accepted(worker):
    class OtherApproval(ApprovedHTTPDestination):
        pass

    planner, _, _, _, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    value = OtherApproval(**approved.__dict__)
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, value)
    safe_error(caught.value, "invalid_request")


@pytest.mark.asyncio
async def test_constructed_scope_cannot_bypass_strict_validation_using_equality(worker, recwarn):
    class EqualValue:
        def __eq__(self, other):
            return True

        def __repr__(self):
            return PRIVATE

    planner, _, _, _, _ = worker
    approved = await planner.prepare_credential_free(SCOPE, REQUEST)
    malformed = Scope.model_construct(organization_id=EqualValue(), environment_id="dev")
    assert malformed == SCOPE
    with pytest.raises(DestinationError) as caught:
        planner.revalidate_credential_free(SCOPE, REQUEST, replace(approved, scope=malformed))
    safe_error(caught.value, "invalid_request")
    assert not recwarn


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["prepare", "prepare_credential_free"])
async def test_suppressed_caller_cancellation_never_returns_approval(method, worker, monkeypatch):
    planner, _, _, resolver, _ = worker
    entered = asyncio.Event()

    async def suppress(host):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ("8.8.8.8",)

    monkeypatch.setattr(resolver, "resolve_ipv4", suppress)
    task = asyncio.create_task(getattr(planner, method)(SCOPE, REQUEST))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["prepare", "prepare_credential_free"])
async def test_suppressed_timeout_remains_a_safe_destination_error(method, worker, monkeypatch):
    planner, _, _, resolver, _ = worker
    planner.dns_timeout_seconds = 0.01

    async def suppress(host):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ("8.8.8.8",)

    monkeypatch.setattr(resolver, "resolve_ipv4", suppress)
    with pytest.raises(DestinationError) as caught:
        await getattr(planner, method)(SCOPE, REQUEST)
    safe_error(caught.value, "unavailable")


@pytest.mark.asyncio
async def test_late_dns_answer_cannot_approve_after_delayed_timeout_callback(worker, monkeypatch):
    planner, _, _, resolver, _ = worker
    planner.dns_timeout_seconds = 0.005

    async def late(host):
        time.sleep(0.02)
        return ("8.8.8.8",)

    monkeypatch.setattr(resolver, "resolve_ipv4", late)
    with pytest.raises(DestinationError) as caught:
        await planner.prepare_credential_free(SCOPE, REQUEST)
    safe_error(caught.value, "unavailable")
