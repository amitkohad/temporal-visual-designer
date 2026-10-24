"""Real project/catalog/activation providers with explicit destination fakes."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.durable_runtime.starts import (
    SQLiteStartRepository,
    StartRequest,
    StartService,
)
from nexusflow.modules.package_promotion.readiness import ProofActivationReadiness
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.service import ActivatedReleaseResolver, ActivationService
from nexusflow.modules.package_promotion.types import ActivationError, ActivationRequest
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ResolvedArtifact, VersioningError
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator

SCOPE = Scope(organization_id="acme", environment_id="dev")


class Authorization:
    def __init__(self):
        self.denied = False
        self.calls = []

    def authorize(self, scope, permission, project):
        self.calls.append((scope, permission, project))
        if self.denied:
            raise PermissionError("Denied")
        return "author-1"


class Configurations:
    def __init__(self):
        self.available = True

    def supports(self, scope, project, revision, schema):
        return self.available


class Capabilities:
    def __init__(self):
        self.available = True

    def supports(self, scope, manifest):
        return self.available


class Artifacts:
    def resolve(self, reference):
        return ResolvedArtifact(reference)


def content(version="A", required_input=False):
    workflows = []
    for name in ("onboarding", "purchase"):
        workflows.append(
            {
                "schema_version": "0.1",
                "workflow_id": name,
                "input_schema": {
                    "type": "object",
                    "properties": {"employee": {"type": "string"}},
                    "required": ["employee"],
                }
                if required_input
                else {"type": "object"},
                "output_schema": {"type": "object"},
                "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
                "nodes": [
                    {
                        "node_id": "start",
                        "kind": "start",
                        "named_ports": {"inputs": [], "outputs": ["out"]},
                        "input_mapping": {},
                    },
                    {
                        "node_id": "end",
                        "kind": "end",
                        "named_ports": {"inputs": ["in"], "outputs": []},
                        "input_mapping": {},
                    },
                ],
                "edges": [
                    {
                        "source_node_id": "start",
                        "source_port": "out",
                        "target_node_id": "end",
                        "target_port": "in",
                    }
                ],
                "error_routes": [],
            }
        )
    for workflow in workflows:
        workflow["input_schema"]["description"] = version
    return {"schema_version": "0.1", "workflows": workflows, "layout": {"version": version}}


@pytest.fixture
def system(tmp_path):
    projects = SQLiteProjectRepository(tmp_path / "projects.db")
    activations = SQLiteActivationRepository(tmp_path / "activations.db")
    catalog = SQLiteCatalogRepository(tmp_path / "catalog.db")
    auth, configurations, capabilities = Authorization(), Configurations(), Capabilities()
    publisher = ProjectService(projects, auth, BuiltinContentValidator(), Artifacts())
    first = publisher.create(SCOPE, "hr", "HR", json.dumps(content()))
    a = publisher.publish(SCOPE, "hr", first.number, "a")
    second = publisher.save(SCOPE, "hr", first.number, json.dumps(content("B")), "Second")
    b = publisher.publish(SCOPE, "hr", second.number, "b")
    readiness = ProofActivationReadiness(catalog, capabilities, configurations)
    service = ActivationService(activations, projects, auth, readiness)
    yield service, publisher, a, b, tmp_path
    projects.close()
    activations.close()
    catalog.close()


def request(release, revision=0, operation="activate-a", **kwargs):
    return ActivationRequest(
        project_id="hr",
        release_id=release.release_id,
        configuration_revision="cfg-1",
        operation_id=operation,
        expected_revision=revision,
        **kwargs,
    )


def test_project_activation_rollback_and_new_start_pinning(system):
    service, _, a, b, path = system
    resolver = ActivatedReleaseResolver(service.repository)
    with pytest.raises(ActivationError):
        resolver.resolve(SCOPE, "hr", "purchase")
    assert service.current(SCOPE, "hr") is None
    first = service.apply(SCOPE, request(a))
    assert first.revision == 1 and first.previous_release_id is None

    class StartAuthorization:
        def authorize(self, scope, project):
            pass

    class Dispatcher:
        def __init__(self):
            self.plans = {}

        def start(self, execution, plan):
            self.plans[execution] = plan

    repository = SQLiteStartRepository(path / "starts.db")
    dispatcher = Dispatcher()
    starts = StartService(repository, StartAuthorization(), resolver, dispatcher)
    old_request = StartRequest(SCOPE, "hr", "onboarding", "old")
    try:
        old = starts.start(old_request)
        service.apply(SCOPE, request(b, 1, "activate-b"))
        for workflow in ("onboarding", "purchase"):
            binding, _ = resolver.resolve(SCOPE, "hr", workflow)
            assert binding.release_id == b.release_id
        new = starts.start(replace(old_request, idempotency_key="new"))
        service.apply(SCOPE, request(a, 2, "rollback-a", action="rollback"))
        assert starts.start(old_request) == old
        assert starts.start(replace(old_request, idempotency_key="new")) == new
        assert old.plan.binding.release_id == a.release_id
        assert new.plan.binding.release_id == b.release_id
        assert (
            starts.start(replace(old_request, idempotency_key="after")).plan.binding.release_id
            == a.release_id
        )
        events = service.events(SCOPE, "hr")
        assert [event.action for event in events] == ["activate", "activate", "rollback"]
        assert events[-1].previous_release_id == b.release_id
        assert all(
            event.actor_id == "author-1" and event.outcome == "succeeded" for event in events
        )
    finally:
        repository.close()


def test_idempotent_retry_after_new_activation_returns_original_receipt(system):
    service, _, a, b, _ = system
    first = service.apply(SCOPE, request(a))
    service.apply(SCOPE, request(b, 1, "b"))
    service.readiness.configurations.available = False
    assert service.apply(SCOPE, request(a)) == first
    assert service.current(SCOPE, "hr").release == b
    assert len(service.events(SCOPE, "hr")) == 2
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(b))
    assert error.value.code == "conflict"


def test_concurrent_connections_have_one_revision_winner(system):
    service, _, a, b, path = system
    second_repo = SQLiteActivationRepository(path / "activations.db")
    second = ActivationService(
        second_repo, service.releases, service.authorization, service.readiness
    )
    barrier = Barrier(2)
    original = service.readiness.validate

    def validate(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    service.readiness.validate = validate

    def apply(pair):
        provider, release, operation = pair
        try:
            return provider.apply(SCOPE, request(release, operation=operation))
        except ActivationError as error:
            return error.code

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(apply, [(service, a, "first"), (second, b, "second")]))
        assert results.count("conflict") == 1
        current = service.current(SCOPE, "hr")
        assert current.revision == 1
        assert sum(event.outcome == "succeeded" for event in service.events(SCOPE, "hr")) == 1
    finally:
        second_repo.close()


def test_failure_after_commit_reconciles_original_operation(system):
    service, _, a, _, _ = system
    original = service.repository.commit

    def lost_ack(*args):
        original(*args)
        raise OSError("response lost")

    service.repository.commit = lost_ack
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(a))
    assert error.value.code == "unavailable"
    service.repository.commit = original
    assert service.apply(SCOPE, request(a)).revision == 1
    assert len(service.events(SCOPE, "hr")) == 1


def test_rollback_rechecks_readiness_and_requires_prior_activation(system):
    service, _, a, b, _ = system
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(a, action="rollback"))
    assert error.value.code == "not_found"
    service.apply(SCOPE, request(a))
    service.apply(SCOPE, request(b, 1, "b"))
    service.readiness.configurations.available = False
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(a, 2, "rollback", action="rollback"))
    assert error.value.code == "incompatible_release"
    assert service.current(SCOPE, "hr").release == b


def test_reauthorize_retries_and_protect_reads(system):
    service, _, a, _, _ = system
    service.apply(SCOPE, request(a))
    service.authorization.denied = True
    for operation in (
        lambda: service.apply(SCOPE, request(a)),
        lambda: service.current(SCOPE, "hr"),
        lambda: service.events(SCOPE, "hr"),
    ):
        with pytest.raises(PermissionError):
            operation()


def test_scope_mismatch_and_missing_release_cannot_activate(system):
    service, _, a, _, _ = system
    original = service.releases.release
    service.releases.release = lambda *args: a
    foreign = Scope(organization_id="other", environment_id="dev")
    with pytest.raises(ActivationError) as error:
        service.apply(foreign, request(a))
    assert error.value.code == "invalid_release"
    assert service.current(foreign, "hr") is None
    service.releases.release = original
    with pytest.raises(ActivationError):
        service.apply(SCOPE, request(a).model_copy(update={"release_id": "missing"}))
    assert service.current(SCOPE, "hr") is None


def test_restart_recovers_active_pointer_and_retains_old_releases(system):
    service, publisher, a, b, path = system
    service.apply(SCOPE, request(a))
    service.apply(SCOPE, request(b, 1, "b"))
    for release in (a, b):
        with pytest.raises(VersioningError) as error:
            publisher.delete_release(SCOPE, "hr", release.release_id)
        assert error.value.code == "referenced"
    reopened = SQLiteActivationRepository(path / "activations.db")
    try:
        assert reopened.current(SCOPE, "hr").release == b
        assert reopened.receipt(SCOPE, request(a)).release == a
        assert (
            ActivatedReleaseResolver(reopened).resolve(SCOPE, "hr", "onboarding")[0].release_id
            == b.release_id
        )
    finally:
        reopened.close()


def test_required_business_input_does_not_block_activation_but_start_validates_it(system):
    service, publisher, _, _, path = system
    revision = publisher.read(SCOPE, "hr")
    changed = publisher.save(
        SCOPE, "hr", revision.number, json.dumps(content(required_input=True)), "Required input"
    )
    release = publisher.publish(SCOPE, "hr", changed.number, "required")
    service.apply(SCOPE, request(release))
    binding, definition = ActivatedReleaseResolver(service.repository).resolve(
        SCOPE, "hr", "onboarding"
    )
    from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan

    with pytest.raises(PlanError):
        compile_plan(definition, binding, SCOPE)
    assert compile_plan(definition, binding, SCOPE, inputs={"employee": "Ada"})


@pytest.mark.parametrize("mutation", ["digest", "artifacts", "runtime"])
def test_integrity_and_runtime_incompatibility_have_no_active_pointer(system, mutation):
    service, _, a, _, _ = system
    if mutation == "digest":
        service.releases.release = lambda *args: replace(a, executable_digest="sha256:" + "0" * 64)
    elif mutation == "artifacts":
        service.releases.release = lambda *args: replace(a, artifacts_json='[{"invalid":true}]')
    req = request(a, runtime_version="future" if mutation == "runtime" else "proof.v1")
    with pytest.raises(ActivationError):
        service.apply(SCOPE, req)
    assert service.current(SCOPE, "hr") is None
    assert not any(event.outcome == "succeeded" for event in service.events(SCOPE, "hr"))


def test_retention_failure_preserves_pointer_and_audits_unavailable(system):
    service, _, a, _, _ = system

    def unavailable(*args):
        raise OSError("storage down")

    service.readiness.retain = unavailable
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(a))
    assert error.value.code == "unavailable"
    assert service.current(SCOPE, "hr") is None
    assert service.events(SCOPE, "hr")[-1].outcome == "unavailable"


def test_readers_see_previous_snapshot_until_commit(system):
    from threading import Event

    service, _, a, b, path = system
    service.apply(SCOPE, request(a))
    reader = SQLiteActivationRepository(path / "activations.db")
    ready, proceed = Event(), Event()
    original = service.repository._event

    def before_commit(*args):
        original(*args)
        ready.set()
        assert proceed.wait(timeout=10)

    service.repository._event = before_commit
    try:
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(service.apply, SCOPE, request(b, 1, "b"))
            try:
                assert ready.wait(timeout=10)
                assert reader.current(SCOPE, "hr").release == a
            finally:
                proceed.set()
            assert result.result(timeout=10).release == b
        assert reader.current(SCOPE, "hr").release == b
    finally:
        reader.close()


def test_transaction_failure_does_not_leave_pointer_receipt_or_success_audit(system):
    service, _, a, _, _ = system
    original = service.repository._event

    def fail(*args):
        raise OSError("audit storage down")

    service.repository._event = fail
    with pytest.raises(ActivationError):
        service.apply(SCOPE, request(a))
    assert service.repository.current(SCOPE, "hr") is None
    assert service.repository.receipt(SCOPE, request(a)) is None
    assert service.repository.events(SCOPE, "hr") == ()
    service.repository._event = original
    assert service.apply(SCOPE, request(a)).revision == 1


def test_concurrent_identical_operation_returns_one_receipt(system):
    service, _, a, _, path = system
    other_repo = SQLiteActivationRepository(path / "activations.db")
    other = ActivationService(
        other_repo, service.releases, service.authorization, service.readiness
    )
    barrier = Barrier(2)
    original = service.readiness.validate

    def validate(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    service.readiness.validate = validate
    try:
        with ThreadPoolExecutor(2) as pool:
            receipts = list(
                pool.map(lambda provider: provider.apply(SCOPE, request(a)), [service, other])
            )
        assert receipts[0] == receipts[1]
        assert len(service.events(SCOPE, "hr")) == 1
    finally:
        other_repo.close()


def test_activation_preserves_destination_configuration_revision(system):
    service, _, a, b, _ = system
    service.apply(SCOPE, request(a))
    service.apply(SCOPE, request(b, 1, "b").model_copy(update={"configuration_revision": "cfg-2"}))
    binding, _ = ActivatedReleaseResolver(service.repository).resolve(SCOPE, "hr", "purchase")
    assert binding.configuration_revision == "cfg-2"
    service.apply(SCOPE, request(a, 2, "rollback", action="rollback"))
    assert (
        ActivatedReleaseResolver(service.repository)
        .resolve(SCOPE, "hr", "purchase")[0]
        .configuration_revision
        == "cfg-1"
    )


def test_reference_input_schema_is_rejected_at_activation(system):
    service, publisher, _, _, _ = system
    payload = content()
    payload["workflows"][0]["input_schema"] = {
        "$defs": {"Input": {"type": "object"}},
        "$ref": "#/$defs/Input",
    }
    revision = publisher.read(SCOPE, "hr")
    revision = publisher.save(SCOPE, "hr", revision.number, json.dumps(payload), "Reference schema")
    release = publisher.publish(SCOPE, "hr", revision.number, "reference-schema")
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(release))
    assert error.value.code == "incompatible_release"
    assert any(issue.code == "unsupported_schema" for issue in error.value.issues)


def test_duplicate_committing_between_receipt_lookup_and_revision_read_is_reconciled(system):
    service, _, a, _, path = system
    other_repo = SQLiteActivationRepository(path / "activations.db")
    other = ActivationService(
        other_repo, service.releases, service.authorization, service.readiness
    )
    original = service.repository.current
    committed = []

    def commit_before_revision_read(scope, project):
        service.repository.current = original
        committed.append(other.apply(scope, request(a)))
        return original(scope, project)

    service.repository.current = commit_before_revision_read
    try:
        assert service.apply(SCOPE, request(a)) == committed[0]
        assert len(service.events(SCOPE, "hr")) == 1
    finally:
        service.repository.current = original
        other_repo.close()


def test_large_input_schema_cannot_bypass_interpreter_definition_budget(system):
    service, publisher, _, _, _ = system
    payload = content()
    payload["workflows"][0]["input_schema"]["description"] = "x" * 131072
    revision = publisher.read(SCOPE, "hr")
    revision = publisher.save(
        SCOPE, "hr", revision.number, json.dumps(payload), "Large input schema"
    )
    release = publisher.publish(SCOPE, "hr", revision.number, "large-schema")
    with pytest.raises(ActivationError) as error:
        service.apply(SCOPE, request(release))
    assert error.value.code == "incompatible_release"
    assert any(issue.code == "definition_limit" for issue in error.value.issues)
    assert service.current(SCOPE, "hr") is None
