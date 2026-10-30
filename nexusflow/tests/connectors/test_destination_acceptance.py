"""Independent worker destination acceptance with real immutable connection storage."""

import asyncio
import json
import socket
import warnings
from dataclasses import replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import DestinationError, HTTPDestinationRequest
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.tests.connectors.test_definitions import definition

SCOPE = Scope(organization_id="acme", environment_id="dev")
REFERENCE = "credential-reference-sentinel"
PRIVATE = "never-echo-query-value"


class WorkloadAuthorization:
    def __init__(self, events):
        self.events = events
        self.denied = False

    def authorize(self, scope, permission, resource_id):
        self.events.append(("authorize", scope, permission, resource_id))
        assert permission == "worker.execute" and resource_id == "hr-api"
        if self.denied:
            raise PermissionError(PRIVATE)
        return "worker-1"


class DNS:
    def __init__(self, events):
        self.events = events
        self.answers = {
            "api.example.com": ("8.8.8.8", "1.1.1.1"),
            "other.example.com": ("9.9.9.9",),
        }
        self.calls = []

    async def resolve_ipv4(self, host):
        self.events.append(("resolve", host))
        self.calls.append(host)
        return self.answers[host]


@pytest.fixture
def worker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only injected fake DNS is permitted; no network transport exists")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    metadata = repository.put(
        SCOPE,
        "hr-api",
        "v1",
        definition(allowed_origins=["https://api.example.com", "https://other.example.com:8443"]),
        "admin",
    )
    events = []
    auth, dns = WorkloadAuthorization(events), DNS(events)
    original_get = repository.get

    def get(*args):
        events.append(("get", *args))
        return original_get(*args)

    monkeypatch.setattr(repository, "get", Mock(side_effect=get))
    request = HTTPDestinationRequest(
        connection_id="hr-api",
        connection_revision="v1",
        operation_id="run-1-step-1",
        path="/employees?business=" + PRIVATE,
    )
    try:
        yield (
            HTTPDestinationPlanner(repository, auth, dns),
            repository,
            auth,
            dns,
            request,
            metadata,
            events,
        )
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_real_registry_pins_identity_destination_and_no_credential_or_url_repr(worker):
    planner, repository, _, dns, request, metadata, events = worker
    plan = await planner.prepare(SCOPE, request)
    assert [event[0] for event in events] == ["authorize", "get", "resolve"]
    assert (
        plan.scope == SCOPE and plan.connection_id == "hr-api" and plan.connection_revision == "v1"
    )
    assert plan.definition_digest == metadata.definition_digest
    assert plan.operation_id == request.operation_id and plan.redirect_count == 0
    assert plan.url == "https://api.example.com/employees?business=" + PRIVATE
    assert (
        plan.origin == "https://api.example.com"
        and plan.host == "api.example.com"
        and plan.port == 443
    )
    assert plan.ipv4_addresses == ("1.1.1.1", "8.8.8.8")
    assert dns.calls == ["api.example.com"]
    assert not hasattr(plan, "secret_reference")
    assert (
        REFERENCE not in repr(plan) and PRIVATE not in repr(plan) and PRIVATE not in repr(request)
    )
    assert (
        json.loads(repository.get(SCOPE, "hr-api", "v1").definition_json)["secret_reference"]
        == REFERENCE
    )


@pytest.mark.asyncio
async def test_fresh_worker_authorization_denies_before_private_lookup_and_dns(worker):
    planner, repository, auth, dns, request, _, events = worker
    auth.denied = True
    with pytest.raises(DestinationError) as error:
        await planner.prepare(SCOPE, request)
    assert error.value.code == "forbidden" and not error.value.retryable
    assert PRIVATE not in str(error.value)
    repository.get.assert_not_called()
    assert dns.calls == [] and len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, False, "", 42])
async def test_malformed_authorization_success_fails_closed_before_private_lookup(worker, actor):
    planner, repository, auth, dns, request, _, _ = worker
    auth.authorize = Mock(return_value=actor)
    with pytest.raises(DestinationError) as error:
        await planner.prepare(SCOPE, request)
    assert error.value.code == "unavailable"
    repository.get.assert_not_called()
    assert dns.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        SCOPE.model_copy(update={"environment_id": "prod"}),
        SCOPE.model_copy(update={"organization_id": "other"}),
    ],
)
async def test_authorized_foreign_scope_cannot_adopt_a_connection(worker, scope):
    planner, _, _, dns, request, _, _ = worker
    with pytest.raises(DestinationError):
        await planner.prepare(scope, request)
    assert dns.calls == []


@pytest.mark.asyncio
async def test_private_definition_tampering_fails_before_dns(worker):
    planner, repository, _, dns, request, _, _ = worker
    row = repository._db.execute("SELECT definition_json FROM http_connections").fetchone()
    payload = json.loads(row[0])
    payload["base_origin"] = "https://other.example.com:8443"
    repository._db.execute("UPDATE http_connections SET definition_json=?", (json.dumps(payload),))
    repository._db.commit()
    with pytest.raises(DestinationError) as error:
        await planner.prepare(SCOPE, request)
    assert error.value.code == "unavailable" and dns.calls == []
    assert REFERENCE not in str(error.value)


@pytest.mark.asyncio
async def test_redirects_keep_identity_and_reauthorize_and_resolve_each_hop(worker):
    planner, _, _, dns, request, _, events = worker
    first = await planner.prepare(SCOPE, request)
    second = await planner.prepare_redirect(SCOPE, request, first, "../next?business=" + PRIVATE)
    third = await planner.prepare_redirect(
        SCOPE, request, second, "https://other.example.com:8443/final"
    )
    assert second.url == "https://api.example.com/next?business=" + PRIVATE
    assert third.url == "https://other.example.com:8443/final" and third.port == 8443
    assert third.host == "other.example.com" and third.ipv4_addresses == ("9.9.9.9",)
    assert [plan.redirect_count for plan in (first, second, third)] == [0, 1, 2]
    assert all(plan.operation_id == request.operation_id for plan in (first, second, third))
    assert len({plan.definition_digest for plan in (first, second, third)}) == 1
    assert dns.calls == ["api.example.com", "api.example.com", "other.example.com"]
    assert [event[0] for event in events] == ["authorize", "get", "resolve"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "https://blocked.example.com/path",
        "//blocked.example.com/path",
        "http://api.example.com/path",
        "https://user:password@api.example.com/path",
        "/next#fragment",
    ],
)
async def test_invalid_or_unlisted_redirect_does_not_trigger_dns(worker, target):
    planner, _, _, dns, request, _, _ = worker
    first = await planner.prepare(SCOPE, request)
    before = list(dns.calls)
    with pytest.raises(DestinationError) as error:
        await planner.prepare_redirect(SCOPE, request, first, target)
    assert dns.calls == before and not error.value.retryable
    assert target not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "127.0.0.1",
        "169.254.169.254",
        "168.63.129.16",
        "10.0.0.1",
        "100.100.100.200",
        "::1",
        "::ffff:127.0.0.1",
    ],
)
async def test_dns_rebinding_or_mixed_private_answers_deny_entire_redirect(worker, answer):
    planner, _, _, dns, request, _, _ = worker
    first = await planner.prepare(SCOPE, request)
    dns.answers["api.example.com"] = ("8.8.8.8", answer)
    with pytest.raises(DestinationError) as error:
        await planner.prepare_redirect(SCOPE, request, first, "/next")
    assert error.value.code == "blocked_destination" and not error.value.retryable
    assert first.ipv4_addresses == ("1.1.1.1", "8.8.8.8")


@pytest.mark.asyncio
async def test_revocation_and_redirect_budget_are_checked_before_next_resolution(worker):
    _, repository, auth, dns, request, _, _ = worker
    planner = HTTPDestinationPlanner(repository, auth, dns, max_redirects=1)
    first = await planner.prepare(SCOPE, request)
    second = await planner.prepare_redirect(SCOPE, request, first, "/next")
    with pytest.raises(DestinationError) as error:
        await planner.prepare_redirect(SCOPE, request, second, "/later")
    assert error.value.code == "redirect_limit" and len(dns.calls) == 2
    auth.denied = True
    with pytest.raises(DestinationError) as error:
        await planner.prepare_redirect(SCOPE, request, first, "/next")
    assert error.value.code == "forbidden" and len(dns.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", SCOPE.model_copy(update={"environment_id": "prod"})),
        ("connection_id", "other"),
        ("connection_revision", "v2"),
        ("operation_id", "other-operation"),
        ("definition_digest", "sha256:" + "0" * 64),
        ("host", "blocked.example.com"),
        ("origin", "https://blocked.example.com"),
        ("port", 8443),
        ("redirect_count", True),
    ],
)
async def test_previous_plan_binding_cannot_be_crossed_or_forged(worker, field, value):
    planner, _, _, dns, request, _, _ = worker
    first = await planner.prepare(SCOPE, request)
    before = list(dns.calls)
    with pytest.raises(DestinationError):
        await planner.prepare_redirect(SCOPE, request, replace(first, **{field: value}), "/next")
    assert dns.calls == before


@pytest.mark.asyncio
async def test_new_registry_revision_does_not_change_selected_definition(worker):
    planner, repository, _, dns, request, metadata, _ = worker
    repository.put(
        SCOPE,
        "hr-api",
        "v2",
        definition(
            base_origin="https://new.example.com", allowed_origins=["https://new.example.com"]
        ),
        "admin",
    )
    first = await planner.prepare(SCOPE, request)
    assert first.definition_digest == metadata.definition_digest and first.host == "api.example.com"
    assert dns.calls == ["api.example.com"]


@pytest.mark.asyncio
async def test_malformed_request_has_no_serialization_warning_or_private_lookup(worker):
    planner, repository, _, dns, request, _, _ = worker
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(DestinationError) as error:
            await planner.prepare(SCOPE, request.model_copy(update={"path": 123}))
    assert error.value.code == "invalid_request" and caught == []
    assert dns.calls == []
    repository.get.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_is_safe_retryable_without_pointer_or_query_echo(worker):
    planner, _, _, dns, request, _, _ = worker

    async def fail(_host):
        raise RuntimeError(PRIVATE + REFERENCE)

    dns.resolve_ipv4 = fail
    with pytest.raises(DestinationError) as error:
        await planner.prepare(SCOPE, request)
    assert error.value.code == "unavailable" and error.value.retryable
    assert PRIVATE not in str(error.value) and REFERENCE not in repr(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_dns_deadline_and_caller_cancellation_stop_the_injected_attempt(worker, cancel):
    _, repository, auth, dns, request, _, _ = worker
    entered, closed = asyncio.Event(), asyncio.Event()

    async def stall(_host):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    dns.resolve_ipv4 = stall
    planner = HTTPDestinationPlanner(
        repository, auth, dns, dns_timeout_seconds=1 if cancel else 0.01
    )
    task = asyncio.create_task(planner.prepare(SCOPE, request))
    await asyncio.wait_for(entered.wait(), 2)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(DestinationError) as error:
            await task
        assert error.value.code == "unavailable" and error.value.retryable
    assert closed.is_set()
