"""Independent offline email authoring; no mail submission or delivery is implied."""

import asyncio
import json
import smtplib
import socket
from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from nexusflow.apps.catalog_adapters import ScopedCatalog
from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.connectors.activity_descriptor import (
    HTTPJSONDescriptorCatalog,
    http_json_activity_descriptor,
)
from nexusflow.modules.connectors.email_descriptor import (
    EmailTextDescriptorCatalog,
    email_text_activity_artifact,
    email_text_activity_descriptor,
)
from nexusflow.modules.connectors.email_types import (
    EmailAuthoringError,
    EmailTextActivityInput,
    prepare_email_text_message,
)
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ProjectContent, VersioningError
from nexusflow.tests.designer_forms.test_acceptance import binding, project

DEV = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-export-email-content"
PIN = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Offline authoring must not submit email"))
    for module, name in (
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
        (asyncio, "open_connection"),
        (smtplib, "SMTP"),
        (smtplib, "SMTP_SSL"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield forbidden
    forbidden.assert_not_called()


def message(**updates):
    value = {
        "connection_id": "notifications",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": [PRIVATE + "@Example.Test"],
        "subject": PRIVATE,
        "text_body": "Welcome to the organization.\n" + PRIVATE,
    }
    value.update(updates)
    return value


def email_project(inputs=None):
    descriptor = email_text_activity_descriptor()
    reference = descriptor.activity_ref.model_dump(mode="json")
    payload = project(message() if inputs is None else inputs)
    payload["activity_refs"] = [reference]
    payload["connections"] = [
        {
            "connection_id": "notifications",
            "connector_type": "email",
            "configuration_schema": {"type": "object"},
        }
    ]
    payload["workflows"][0]["nodes"][1]["activity_ref"] = reference
    return payload


def validate(payload, catalog=None):
    content = ProjectContent.model_validate_json(json.dumps(payload))
    return AuthoringValidator(catalog or EmailTextDescriptorCatalog()).validate(content)


@pytest.mark.parametrize("mode", ["live", "mock"])
def test_literal_email_is_valid_offline_with_exact_descriptor_pin(mode):
    inputs = message(execution_mode=mode)
    if mode == "mock":
        inputs["fixture_digest"] = PIN
    assert validate(email_project(inputs)) == ()
    prepared = prepare_email_text_message(EmailTextActivityInput.model_validate(inputs))
    assert prepared.to == (PRIVATE + "@example.test",)
    assert prepared.text_body.endswith(PRIVATE)
    assert PRIVATE not in repr(prepared)
    assert PRIVATE not in email_text_activity_artifact().decode()


@pytest.mark.parametrize(
    "updates",
    [
        {"execution_mode": "fallback"},
        {"execution_mode": "mock"},
        {"fixture_digest": PIN},
        {"to": []},
        {"to": [PRIVATE + "@example.test\r\nBcc: other@example.test"]},
        {"to": ["Display <employee@example.test>"]},
        {"subject": PRIVATE + "\r\nBcc: other@example.test"},
        {"subject": "\t"},
        {"text_body": PRIVATE + "\x00"},
        {"headers": {"authorization": PRIVATE}},
        {"sender": PRIVATE + "@example.test"},
        {"smtp_host": PRIVATE + ".test"},
        {"credentials": PRIVATE},
        {"scope": {"organization_id": "foreign", "environment_id": "prod"}},
        {"operation_id": PRIVATE},
    ],
)
def test_portable_authoring_rejects_injection_and_untrusted_fields_privately(updates):
    issues = validate(email_project(message(**updates)))
    assert issues and all(issue.code == "invalid_activity_input" for issue in issues)
    assert PRIVATE not in repr(issues)


def test_backend_enforces_aggregate_and_canonical_recipient_limits_beyond_schema():
    for inputs in (
        message(to=["employee@example.test"], bcc=["employee@EXAMPLE.TEST"]),
        message(
            to=[f"person{index}@example.test" for index in range(50)],
            cc=["overflow@example.test"],
        ),
        message(text_body="é" * (524288 + 1)),
    ):
        # A tampered model must not bypass the preparation boundary. Portable
        # schemas cannot express aggregate/canonical/UTF-8 byte rules fully.
        unsafe = EmailTextActivityInput.model_construct(**inputs)
        with pytest.raises(EmailAuthoringError) as caught:
            prepare_email_text_message(unsafe)
        assert caught.value.code == "invalid_request"
        assert PRIVATE not in str(caught.value) + repr(caught.value)


def test_prepared_private_snapshot_is_independent_of_mutable_authoring_lists():
    typed = EmailTextActivityInput.model_validate(message(cc=["manager@example.test"]))
    prepared = prepare_email_text_message(typed)
    typed.to[0] = "different@example.test"
    typed.cc.clear()
    assert prepared.to == (PRIVATE + "@example.test",)
    assert prepared.cc == ("manager@example.test",)
    with pytest.raises(FrozenInstanceError):
        prepared.subject = "changed"
    assert PRIVATE not in repr(typed) + repr(prepared)


def test_http_and_email_are_independently_selectable_without_default_registration():
    class ExplicitDescriptors:
        def descriptor(self, reference):
            return EmailTextDescriptorCatalog().descriptor(
                reference
            ) or HTTPJSONDescriptorCatalog().descriptor(reference)

    payload = email_project()
    http = http_json_activity_descriptor()
    http_inputs = {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "live",
        "path": "/employees",
    }
    http_workflow = deepcopy(project(http_inputs)["workflows"][0])
    http_workflow["workflow_id"] = "purchase-requisition"
    http_workflow["nodes"][1]["activity_ref"] = http.activity_ref.model_dump(mode="json")
    payload["workflows"].append(http_workflow)
    payload["activity_refs"].append(http.activity_ref.model_dump(mode="json"))
    payload["connections"].append(
        {
            "connection_id": "hr",
            "connector_type": "http",
            "configuration_schema": {"type": "object"},
        }
    )
    assert validate(payload, ExplicitDescriptors()) == ()
    email = email_text_activity_descriptor()
    assert email.activity_ref != http.activity_ref
    assert email.connection_requirements == ("email",)
    assert http.connection_requirements == ("http",)
    assert EmailTextDescriptorCatalog().descriptor(http.activity_ref) is None
    assert HTTPJSONDescriptorCatalog().descriptor(email.activity_ref) is None
    payload["workflows"][1]["nodes"][1]["input_mapping"] = message()
    assert any(
        issue.code == "invalid_activity_input" for issue in validate(payload, ExplicitDescriptors())
    )


@pytest.mark.parametrize("field,value", [("version", "2.0.0"), ("artifact_digest", PIN)])
def test_wrong_descriptor_pin_cannot_authorize_offline_email_mapping(field, value):
    payload = email_project()
    reference = dict(payload["activity_refs"][0], **{field: value})
    payload["activity_refs"] = [reference]
    payload["workflows"][0]["nodes"][1]["activity_ref"] = reference
    issues = validate(payload)
    assert issues and any(issue.code == "incompatible_activity" for issue in issues)
    assert PRIVATE not in repr(issues)


def test_variable_bindings_remain_explicitly_outside_this_literal_authoring_subset():
    payload = email_project(message(subject=binding("input", "/employee")))
    issues = validate(payload)
    assert any(issue.code == "unsupported_mapping_schema" for issue in issues)
    assert PRIVATE not in repr(issues)


def test_real_python_catalog_blocks_metadata_publication_and_has_no_capability_mutation(tmp_path):
    descriptor = email_text_activity_descriptor()
    authorization = Mock()
    authorization.authorize.return_value = "business-author"
    capabilities = Mock()
    catalog_repository = SQLiteCatalogRepository(tmp_path / "catalog.db")
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    projects_repository = SQLiteProjectRepository(tmp_path / "projects.db")
    try:
        catalog = ScopedCatalog(
            CatalogService(catalog_repository, intake, authorization, capabilities), DEV
        )
        assert catalog.resolve(descriptor.activity_ref) is None
        assert catalog.descriptor(descriptor.activity_ref) is None
        service = ProjectService(
            projects_repository,
            authorization,
            AuthoringValidator(EmailTextDescriptorCatalog()),
            catalog,
        )
        revision = service.create(DEV, "onboarding", "Onboarding", canonical(email_project()))
        with pytest.raises(VersioningError) as caught:
            service.publish(DEV, "onboarding", revision.number, "email-release")
        assert caught.value.code == "validation_failed"
        assert any(issue.code == "missing_dependency" for issue in caught.value.issues)
        assert PRIVATE not in str(caught.value) + repr(caught.value.issues)
        assert capabilities.mock_calls == []
        assert not any(
            hasattr(EmailTextDescriptorCatalog(), method)
            for method in ("resolve", "execute", "publish", "search")
        )
    finally:
        projects_repository.close()
        intake.close()
        catalog_repository.close()
