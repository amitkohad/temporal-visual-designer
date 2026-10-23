"""Start reliability against real SQLite and independent upstream fakes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Lock

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.starts import (
    SQLiteStartRepository,
    StartError,
    StartRequest,
    StartService,
)
from nexusflow.modules.durable_runtime.types import ReleaseBinding


class Authorization:
    denied = False

    def authorize(self, scope, project):
        if self.denied:
            raise PermissionError("Denied")


class Releases:
    def __init__(self):
        self.binding = ReleaseBinding("acme", "dev", "release-a", "sha256:" + "a" * 64, "cfg-a")
        self.calls = 0
        self.definition = (
            Path(__file__).parents[1] / "fixtures/purchasing.workflow.json"
        ).read_text()

    def resolve(self, scope, project, workflow):
        self.calls += 1
        return self.binding, self.definition


class Dispatcher:
    def __init__(self):
        self.executions = {}
        self.calls = 0
        self.lose_ack = False
        self.lock = Lock()

    def start(self, execution_id, plan):
        with self.lock:
            self.calls += 1
            previous = self.executions.setdefault(execution_id, plan)
            assert previous == plan
            if self.lose_ack:
                raise OSError("ack lost after start")


@pytest.fixture
def setup(tmp_path):
    repository = SQLiteStartRepository(tmp_path / "starts.db")
    authorization, releases, dispatcher = Authorization(), Releases(), Dispatcher()
    request = StartRequest(
        Scope(organization_id="acme", environment_id="dev"),
        "hr",
        "purchase-requisition",
        "request-1",
    )
    service = StartService(repository, authorization, releases, dispatcher)
    yield service, request, tmp_path / "starts.db"
    repository.close()


def test_retry_pins_release_and_configuration_even_after_activation_changes(setup):
    service, request, _ = setup
    first = service.start(request)
    service.releases.binding = replace(
        service.releases.binding, release_id="release-b", configuration_revision="cfg-b"
    )
    assert service.start(request) == first
    second = service.start(replace(request, idempotency_key="request-2"))
    assert second.plan.binding.release_id == "release-b"
    assert first.plan.binding.configuration_revision == "cfg-a"
    assert first.execution_id != second.execution_id
    assert service.dispatcher.calls == 2


def test_canonical_input_retry_and_conflicting_payload(setup):
    service, request, _ = setup
    first = service.start(replace(request, input_json='{"a":1,"b":2}'))
    assert service.start(replace(request, input_json=' {"b":2, "a":1} ')) == first
    with pytest.raises(StartError) as error:
        service.start(replace(request, input_json='{"a":2}'))
    assert error.value.code == "conflict"
    assert service.dispatcher.calls == 1


def test_lost_ack_and_process_restart_reuse_exact_plan(setup):
    service, request, database = setup
    service.dispatcher.lose_ack = True
    with pytest.raises(StartError) as error:
        service.start(request)
    assert error.value.code == "unavailable"
    service.releases.binding = replace(service.releases.binding, release_id="release-b")
    service.dispatcher.lose_ack = False
    reopened = SQLiteStartRepository(database)
    try:
        recovered = StartService(
            reopened, service.authorization, service.releases, service.dispatcher
        )
        receipt = recovered.start(request)
        assert receipt.plan.binding.release_id == "release-a"
        assert len(service.dispatcher.executions) == 1
        assert service.releases.calls == 1
        assert recovered.start(request) == receipt
        assert service.dispatcher.calls == 2
    finally:
        reopened.close()


def test_reauthorization_precedes_retry_lookup(setup):
    service, request, _ = setup
    service.start(request)
    service.authorization.denied = True
    with pytest.raises(PermissionError):
        service.start(request)
    assert service.dispatcher.calls == 1


@pytest.mark.parametrize(
    "payload",
    ["[]", "{", '{"secret":NaN}', '"text"', '{"x":"' + "x" * 65536 + '"}'],
    ids=["array", "malformed", "nonfinite", "string", "oversized"],
)
def test_invalid_input_has_zero_resolver_or_dispatch_effects(setup, payload):
    service, request, _ = setup
    with pytest.raises(StartError) as error:
        service.start(replace(request, input_json=payload))
    assert error.value.code == "invalid_input"
    assert service.releases.calls == service.dispatcher.calls == 0


def test_wrong_scope_release_rejected_before_persistence(setup):
    service, request, _ = setup
    service.releases.binding = replace(service.releases.binding, organization_id="other")
    with pytest.raises(PermissionError):
        service.start(request)
    assert not service.dispatcher.executions


@pytest.mark.parametrize(
    "changes", [{"source": "webhook"}, {"idempotency_key": ""}, {"workflow_id": "bad/id"}]
)
def test_unsupported_source_and_identifiers_rejected(setup, changes):
    service, request, _ = setup
    with pytest.raises(StartError):
        service.start(replace(request, **changes))
    assert service.releases.calls == service.dispatcher.calls == 0


def test_concurrent_connections_reserve_one_execution(setup):
    service, request, database = setup
    other = SQLiteStartRepository(database)
    barrier = Barrier(2)
    original = service.releases.resolve

    def resolve(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    service.releases.resolve = resolve
    second = StartService(other, service.authorization, service.releases, service.dispatcher)
    try:
        with ThreadPoolExecutor(2) as pool:
            receipts = list(pool.map(lambda item: item.start(request), [service, second]))
        assert receipts[0] == receipts[1]
        assert len(service.dispatcher.executions) == 1
    finally:
        other.close()


def test_project_and_source_are_separate_idempotency_domains(setup):
    service, request, _ = setup
    receipts = [
        service.start(item)
        for item in (
            request,
            replace(request, project_id="finance"),
            replace(request, source="api"),
        )
    ]
    assert len({receipt.execution_id for receipt in receipts}) == 3


def test_scope_keys_do_not_collide(setup):
    service, request, _ = setup
    first = service.start(request)
    service.releases.binding = replace(service.releases.binding, organization_id="other")
    other = replace(request, scope=Scope(organization_id="other", environment_id="dev"))
    second = service.start(other)
    assert second.execution_id != first.execution_id
    assert second.plan.binding.organization_id == "other"
    service.releases.binding = replace(service.releases.binding, environment_id="uat")
    third = service.start(
        replace(other, scope=Scope(organization_id="other", environment_id="uat"))
    )
    assert len({first.execution_id, second.execution_id, third.execution_id}) == 3


def test_invalid_plan_does_not_consume_key(setup):
    service, request, _ = setup
    original = service.releases.definition
    service.releases.definition = "{}"
    with pytest.raises(StartError) as error:
        service.start(request)
    assert error.value.code == "invalid_input"
    assert service.dispatcher.calls == 0
    service.releases.definition = original
    assert service.start(request).dispatched


def test_failure_after_dispatch_before_local_ack_is_retryable(setup):
    service, request, _ = setup
    acknowledge = service.repository.acknowledge

    def crash(execution_id):
        raise OSError("disk unavailable")

    service.repository.acknowledge = crash
    with pytest.raises(OSError):
        service.start(request)
    service.repository.acknowledge = acknowledge
    assert service.start(request).dispatched
    assert service.dispatcher.calls == 2
    assert len(service.dispatcher.executions) == 1


def test_concurrent_conflicting_requests_have_one_winner(setup):
    service, request, database = setup
    other = SQLiteStartRepository(database)
    second = StartService(other, service.authorization, service.releases, service.dispatcher)
    barrier = Barrier(2)
    original = service.releases.resolve

    def resolve(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    service.releases.resolve = resolve

    def start(pair):
        provider, payload = pair
        try:
            return provider.start(replace(request, input_json=payload))
        except StartError as error:
            return error.code

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(start, [(service, '{"a":1}'), (second, '{"a":2}')]))
        assert results.count("conflict") == 1
        assert len(service.dispatcher.executions) == 1
    finally:
        other.close()
