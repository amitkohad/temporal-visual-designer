"""Pinned worker planning: no dispatch, credential resolution or implicit DNS."""

import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import DestinationError, HTTPDestinationRequest
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.types import HTTPConnectionWrite

SCOPE = Scope(organization_id="acme", environment_id="dev")


class Authorization:
    error = None

    def __init__(self):
        self.calls = []

    def authorize(self, scope, permission, resource):
        self.calls.append((scope, permission, resource))
        if self.error:
            raise self.error
        return "worker"


class Resolver:
    answer = ("8.8.8.8",)
    error = None

    def __init__(self):
        self.calls = []

    async def resolve_ipv4(self, host):
        self.calls.append(host)
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def planner(tmp_path):
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    repository.put(
        SCOPE,
        "api",
        "v1",
        HTTPConnectionWrite(
            base_origin="https://api.example.com",
            allowed_origins=("https://api.example.com", "https://second.example.com"),
            secret_reference="credential-reference-never-return",
        ),
        "admin",
    )
    repository.get = Mock(wraps=repository.get)
    auth, resolver = Authorization(), Resolver()
    selected = HTTPDestinationPlanner(repository, auth, resolver)
    request = HTTPDestinationRequest(
        connection_id="api",
        connection_revision="v1",
        operation_id="effect-1",
        path="/business?value=private-query-value",
    )
    try:
        yield selected, request, repository, auth, resolver
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_plan_is_bound_to_connection_and_keeps_sensitive_url_out_of_repr(planner):
    selected, request, repository, auth, resolver = planner
    result = await selected.prepare(SCOPE, request)
    assert result.scope == SCOPE and result.operation_id == "effect-1"
    assert result.url == "https://api.example.com/business?value=private-query-value"
    assert result.host == "api.example.com" and result.port == 443
    assert result.ipv4_addresses == ("8.8.8.8",) and result.redirect_count == 0
    assert result.definition_digest == repository.get(SCOPE, "api", "v1").metadata.definition_digest
    assert auth.calls == [(SCOPE, "worker.execute", "api")]
    assert resolver.calls == ["api.example.com"]
    assert "private-query-value" not in repr(result) + repr(request)
    assert "credential-reference-never-return" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [(PermissionError("private-auth"), "forbidden"), (RuntimeError("private-auth"), "unavailable")],
)
async def test_auth_failure_precedes_private_lookup_and_dns(planner, error, code):
    selected, request, repository, auth, resolver = planner
    auth.error = error
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == code and failure.value.retryable == (code == "unavailable")
    assert "private-auth" not in str(failure.value)
    repository.get.assert_not_called()
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["scope", "digest", "presence", "duplicate", "oversize", "type"])
async def test_bad_pinned_connection_fails_before_dns(planner, change):
    selected, request, repository, _, resolver = planner
    record = repository.get(SCOPE, "api", "v1")
    if change == "scope":
        record = replace(
            record,
            metadata=record.metadata.model_copy(
                update={"scope": SCOPE.model_copy(update={"environment_id": "prod"})}
            ),
        )
    elif change == "digest":
        value = json.loads(record.definition_json)
        value["secret_reference"] = "other-reference"
        record = replace(record, definition_json=json.dumps(value))
    elif change == "presence":
        record = replace(
            record, metadata=record.metadata.model_copy(update={"has_secret_reference": False})
        )
    elif change == "duplicate":
        record = replace(
            record,
            definition_json='{"base_origin":"https://api.example.com","base_origin":"https://second.example.com"}',
        )
    elif change == "oversize":
        record = replace(record, definition_json="private-value" * 2000)
    else:
        record = object()
    repository.get.return_value = record
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == "unavailable"
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "//second.example.com/path",
        "https://second.example.com/path",
        "relative",
        "/bad\\path",
        "/bad\nvalue",
    ],
)
async def test_invalid_initial_target_never_resolves(planner, path):
    selected, request, _, _, resolver = planner
    with pytest.raises(DestinationError):
        await selected.prepare(SCOPE, request.model_copy(update={"path": path}))
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_redirect_reauthorizes_and_preserves_operation_while_resolving_new_target(planner):
    selected, request, repository, auth, resolver = planner
    first = await selected.prepare(SCOPE, request)
    second = await selected.prepare_redirect(
        SCOPE, request, first, "https://second.example.com/next"
    )
    assert second.url == "https://second.example.com/next"
    assert second.operation_id == first.operation_id
    assert second.definition_digest == first.definition_digest and second.redirect_count == 1
    assert auth.calls == [(SCOPE, "worker.execute", "api")] * 2
    assert repository.get.call_count == 2
    assert resolver.calls == ["api.example.com", "second.example.com"]


@pytest.mark.asyncio
async def test_disallowed_redirect_is_denied_before_new_dns(planner):
    selected, request, _, _, resolver = planner
    first = await selected.prepare(SCOPE, request)
    with pytest.raises(DestinationError) as failure:
        await selected.prepare_redirect(SCOPE, request, first, "https://outside.example.com/secret")
    assert failure.value.code == "blocked_destination"
    assert resolver.calls == ["api.example.com"]


@pytest.mark.asyncio
async def test_dns_rebinding_on_same_origin_redirect_is_rechecked(planner):
    selected, request, _, _, resolver = planner
    first = await selected.prepare(SCOPE, request)
    resolver.answer = ("127.0.0.1",)
    with pytest.raises(DestinationError) as failure:
        await selected.prepare_redirect(SCOPE, request, first, "/next")
    assert failure.value.code == "blocked_destination"
    assert resolver.calls == ["api.example.com", "api.example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", SCOPE.model_copy(update={"environment_id": "prod"})),
        ("connection_id", "other"),
        ("connection_revision", "v2"),
        ("operation_id", "other"),
        ("definition_digest", "bad"),
        ("origin", "https://other.example.com"),
        ("host", "other.example.com"),
        ("port", True),
        ("redirect_count", True),
        ("ipv4_addresses", ("127.0.0.1",)),
    ],
)
async def test_redirect_rejects_foreign_or_forged_previous_plan_without_new_dns(
    planner, field, value
):
    selected, request, _, _, resolver = planner
    first = await selected.prepare(SCOPE, request)
    with pytest.raises(DestinationError) as failure:
        await selected.prepare_redirect(SCOPE, request, replace(first, **{field: value}), "/next")
    assert failure.value.code == "invalid_request"
    assert resolver.calls == ["api.example.com"]


@pytest.mark.asyncio
async def test_redirect_cap_stops_before_new_dns(planner):
    selected, request, _, _, resolver = planner
    selected.max_redirects = 0
    first = await selected.prepare(SCOPE, request)
    with pytest.raises(DestinationError) as failure:
        await selected.prepare_redirect(SCOPE, request, first, "/next")
    assert failure.value.code == "redirect_limit" and not failure.value.retryable
    assert resolver.calls == ["api.example.com"]


@pytest.mark.asyncio
async def test_dns_failure_is_sanitized_and_retryable(planner):
    selected, request, _, _, resolver = planner
    resolver.error = OSError("private-provider-value")
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == "unavailable" and failure.value.retryable
    assert "private-provider-value" not in str(failure.value)


@pytest.mark.asyncio
async def test_timeout_cancels_resolver_and_caller_cancellation_propagates(planner):
    selected, request, _, _, resolver = planner
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def stalled(_host):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    resolver.resolve_ipv4 = stalled
    selected.dns_timeout_seconds = 0.01
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == "unavailable" and cancelled.is_set()
    selected.dns_timeout_seconds = 5
    entered.clear()
    cancelled.clear()
    task = asyncio.create_task(selected.prepare(SCOPE, request))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_resolver_cannot_approve_a_late_answer_by_swallowing_timeout_cancel(planner):
    selected, request, _, _, resolver = planner

    async def late_answer(_host):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ("8.8.8.8",)

    resolver.resolve_ipv4 = late_answer
    selected.dns_timeout_seconds = 0.01
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == "unavailable"


@pytest.mark.asyncio
async def test_late_answer_is_rejected_even_before_timeout_callback_can_run(planner):
    selected, request, _, _, resolver = planner

    async def blocking_answer(_host):
        # A broken adapter can block the event loop, delaying its timeout callback.
        time.sleep(0.02)
        return ("8.8.8.8",)

    resolver.resolve_ipv4 = blocking_answer
    selected.dns_timeout_seconds = 0.01
    with pytest.raises(DestinationError) as failure:
        await selected.prepare(SCOPE, request)
    assert failure.value.code == "unavailable"


@pytest.mark.parametrize(
    "values",
    [
        {"dns_timeout_seconds": True},
        {"dns_timeout_seconds": float("nan")},
        {"dns_timeout_seconds": 0},
        {"dns_timeout_seconds": 31},
        {"max_redirects": True},
        {"max_redirects": -1},
        {"max_redirects": 11},
    ],
)
def test_invalid_host_limits_rejected(planner, values):
    _, _, repository, auth, resolver = planner
    with pytest.raises(ValueError):
        HTTPDestinationPlanner(repository, auth, resolver, **values)
