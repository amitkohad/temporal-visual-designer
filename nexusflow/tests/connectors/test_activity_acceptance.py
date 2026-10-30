"""Independent literal-authoring and real mock composition; no runtime publication claim."""

import asyncio
import hashlib
import json
import socket
from unittest.mock import Mock

import pytest

from nexusflow.apps.catalog_adapters import ScopedCatalog
from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_descriptor import (
    HTTPJSONDescriptorCatalog,
    http_json_activity_artifact,
    http_json_activity_descriptor,
)
from nexusflow.modules.connectors.activity_types import HTTPActivityError, HTTPJSONActivityInput
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_scenarios import StaticHTTPMockScenarios, scenario_digest
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ProjectContent, VersioningError
from nexusflow.tests.connectors import test_execution_acceptance as live_fixture
from nexusflow.tests.connectors import test_transport_acceptance as tls
from nexusflow.tests.designer_forms.test_acceptance import binding, project

DEV = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-export-provider-response"
certificates = tls.certificates


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert scope == DEV and permission == "worker.execute" and connection_id == "hr"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "worker-1"


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    original_open_connection = asyncio.open_connection
    forbidden = Mock(side_effect=AssertionError("Activity acceptance must not access network"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    yield forbidden, original_open_connection
    forbidden.assert_not_called()


def mock_components():
    payload = {"employee": "example", "nested": ["é", True, None]}
    scenario = HTTPMockScenario(
        scope=DEV,
        connection_id="hr",
        connection_revision="fixture-v1",
        expected_path="/employees",
        expected_request=HTTPAttemptRequest(method="POST", body=canonical(payload).encode()),
        status_code=201,
        headers=(("x-private", PRIVATE),),
        body=PRIVATE.encode(),
    )
    worker = Worker()
    executor = HTTPMockExecutor(
        ExplicitDevHTTPMockPolicy(worker, (DEV,)), StaticHTTPMockScenarios((scenario,))
    )
    inputs = {
        "connection_id": "hr",
        "connection_revision": "fixture-v1",
        "execution_mode": "mock",
        "fixture_digest": scenario_digest(scenario),
        "method": "POST",
        "path": "/employees",
        "json_body": payload,
    }
    return worker, HTTPJSONActivityAdapter(mock=executor), inputs


def authoring_payload(inputs):
    descriptor = http_json_activity_descriptor()
    payload = project(inputs)
    reference = descriptor.activity_ref.model_dump(mode="json")
    payload["activity_refs"] = [reference]
    payload["connections"] = [
        {
            "connection_id": "hr",
            "connector_type": "http",
            "configuration_schema": {"type": "object"},
        }
    ]
    payload["workflows"][0]["nodes"][1]["activity_ref"] = reference
    return payload


def authoring_issues(inputs):
    return AuthoringValidator(HTTPJSONDescriptorCatalog()).validate(
        ProjectContent.model_validate_json(json.dumps(authoring_payload(inputs)))
    )


def test_literal_http_activity_is_accepted_by_real_authoring_validator():
    _, _, inputs = mock_components()
    assert authoring_issues(inputs) == ()
    assert (
        authoring_issues(
            {
                "connection_id": "hr",
                "connection_revision": "v1",
                "execution_mode": "live",
                "path": "/health",
            }
        )
        == ()
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"execution_mode": "fallback"},
        {"execution_mode": "live"},
        {"fixture_digest": None},
        {"method": "GET"},
        {"method": "CONNECT"},
        {"scope": {"organization_id": "other", "environment_id": "prod"}},
        {"operation_id": "business-selected-operation"},
        {"path": "https://other.invalid/path"},
    ],
)
def test_authoring_rejects_invalid_combinations_and_untrusted_context_fields(updates):
    _, _, inputs = mock_components()
    inputs.update(updates)
    issues = authoring_issues(inputs)
    assert issues and all(issue.code == "invalid_activity_input" for issue in issues)
    assert all(PRIVATE not in issue.message for issue in issues)


def test_omitted_method_still_enforces_default_get_body_rule():
    _, _, inputs = mock_components()
    inputs.pop("method")
    assert any(issue.code == "invalid_activity_input" for issue in authoring_issues(inputs))


def test_variable_mapping_remains_explicitly_unsupported_for_composed_schema():
    _, _, inputs = mock_components()
    inputs["path"] = binding("input", "/employee")
    assert any(issue.code == "unsupported_mapping_schema" for issue in authoring_issues(inputs))


def test_descriptor_pin_hashes_metadata_bytes_and_is_not_executable_resolution():
    descriptor = http_json_activity_descriptor()
    artifact = http_json_activity_artifact()
    assert (
        descriptor.activity_ref.artifact_digest == "sha256:" + hashlib.sha256(artifact).hexdigest()
    )
    assert canonical(json.loads(artifact)).encode() == artifact
    catalog = HTTPJSONDescriptorCatalog()
    assert catalog.descriptor(descriptor.activity_ref) == descriptor
    assert not hasattr(catalog, "resolve")
    for updates in ({"version": "2.0.0"}, {"artifact_digest": "sha256:" + "0" * 64}):
        assert catalog.descriptor(descriptor.activity_ref.model_copy(update=updates)) is None


def test_existing_python_catalog_does_not_publish_static_metadata(tmp_path):
    _, _, inputs = mock_components()
    descriptor = http_json_activity_descriptor()
    catalog_repository = SQLiteCatalogRepository(tmp_path / "catalog.db")
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    projects_repository = SQLiteProjectRepository(tmp_path / "projects.db")
    authorization = Mock()
    authorization.authorize.return_value = "author-1"
    capabilities = Mock()
    python_catalog = ScopedCatalog(
        CatalogService(catalog_repository, intake, authorization, capabilities), DEV
    )
    try:
        assert python_catalog.resolve(descriptor.activity_ref) is None
        assert python_catalog.descriptor(descriptor.activity_ref) is None
        service = ProjectService(
            projects_repository,
            authorization,
            AuthoringValidator(HTTPJSONDescriptorCatalog()),
            python_catalog,
        )
        revision = service.create(
            DEV, "onboarding", "Onboarding", json.dumps(authoring_payload(inputs))
        )
        with pytest.raises(VersioningError) as caught:
            service.publish(DEV, "onboarding", revision.number, "release-1")
        assert caught.value.code == "validation_failed"
        assert any(issue.code == "missing_dependency" for issue in caught.value.issues)
        capabilities.assert_not_called()
    finally:
        projects_repository.close()
        intake.close()
        catalog_repository.close()


@pytest.mark.asyncio
async def test_real_mock_composition_returns_only_safe_metadata_with_trusted_context():
    worker, adapter, inputs = mock_components()
    typed = HTTPJSONActivityInput.model_validate(inputs)
    result = await adapter.execute(DEV, "run-1-step-1", typed)
    assert result.model_dump() == {
        "operation_id": "run-1-step-1",
        "status_code": 201,
        "execution_mode": "mock",
    }
    assert len(worker.calls) == 2
    assert PRIVATE not in repr(result) + result.model_dump_json()
    assert "headers" not in result.model_dump() and "body" not in result.model_dump()


@pytest.mark.asyncio
async def test_real_mock_repeat_and_concurrent_results_preserve_context_identity():
    _, adapter, inputs = mock_components()
    results = await asyncio.gather(
        *(adapter.execute(DEV, f"operation-{index}", inputs) for index in range(12))
    )
    assert [result.operation_id for result in results] == [f"operation-{i}" for i in range(12)]
    assert {result.status_code for result in results} == {201}
    assert {result.execution_mode for result in results} == {"mock"}


@pytest.mark.asyncio
async def test_real_mock_mismatch_and_revocation_are_private_without_live_fallback():
    worker, adapter, inputs = mock_components()
    changed = dict(inputs, json_body={"employee": "different"})
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(DEV, "operation-1", changed)
    assert caught.value.code == "fixture_mismatch"
    assert not caught.value.delivery_may_have_occurred
    worker.revoked = True
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(DEV, "operation-1", inputs)
    assert caught.value.code == "forbidden" and not caught.value.retryable
    assert PRIVATE not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scope", "operation_id", "headers", "authorization"])
async def test_context_and_credentials_cannot_be_injected_by_business_mapping(field):
    worker, adapter, inputs = mock_components()
    inputs[field] = PRIVATE
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(DEV, "operation-1", inputs)
    assert caught.value.code == "invalid_request" and worker.calls == []
    assert PRIVATE not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
async def test_live_selection_without_live_port_never_uses_real_mock_port():
    worker, adapter, inputs = mock_components()
    inputs["execution_mode"] = "live"
    inputs.pop("fixture_digest")
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(DEV, "operation-1", inputs)
    assert caught.value.code == "unavailable" and worker.calls == []
    assert not caught.value.delivery_may_have_occurred


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_actual_live_registry_tls_preserves_gates_and_drops_provider_response(
    tmp_path, monkeypatch, certificates, forbidden_network, status
):
    # Only the asserted numeric-pin dial is re-enabled and then redirected to
    # loopback by the existing TLS helper; public DNS remains forbidden.
    _, original = forbidden_network
    monkeypatch.setattr(asyncio, "open_connection", original)
    response = (
        f"HTTP/1.1 {status} Result\r\nContent-Length: {len(PRIVATE)}\r\n"
        f"X-Private: {PRIVATE}\r\nConnection: close\r\n\r\n{PRIVATE}"
    ).encode()
    async with tls.endpoint(certificates, response) as (port, names, received, _, _):
        with live_fixture.worker(tmp_path, monkeypatch, port=port) as (
            planner,
            _,
            _,
            _,
            destination,
            events,
        ):
            calls = live_fixture.checked_dial(monkeypatch, events)
            adapter = HTTPJSONActivityAdapter(live=live_fixture.executor(planner, certificates))
            payload = {"business": PRIVATE, "accent": "é"}
            result = await adapter.execute(
                live_fixture.SCOPE,
                "activity-operation-1",
                {
                    "connection_id": destination.connection_id,
                    "connection_revision": destination.connection_revision,
                    "execution_mode": "live",
                    "method": "POST",
                    "path": destination.path,
                    "json_body": payload,
                },
            )
            assert result.model_dump() == {
                "operation_id": "activity-operation-1",
                "status_code": status,
                "execution_mode": "live",
            }
            assert events == [
                "authorize",
                "get",
                "dns",
                "authorize",
                "get",
                "tls",
                "authorize",
                "get",
                "write",
            ]
            assert names == ["api.example.com"] and len(calls) == len(received) == 1
            assert received[0].endswith(canonical(payload).encode())
            assert PRIVATE not in repr(result) + result.model_dump_json()
