"""Offline email authoring metadata; never a transport or delivery test."""

import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from nexusflow.contracts.models import ActivityRef
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.email_descriptor import (
    EmailTextDescriptorCatalog,
    email_text_activity_artifact,
    email_text_activity_descriptor,
)
from nexusflow.modules.project_versioning.canonical import canonical

DIGEST = "sha256:" + "a" * 64


def message(**changes):
    value = {
        "connection_id": "employee-mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["employee@example.test"],
        "subject": "Welcome",
        "text_body": "Welcome to the team.\nYour onboarding is ready.",
    }
    value.update(changes)
    return value


def validator():
    return Draft202012Validator(email_text_activity_descriptor().input_schema)


def test_canonical_metadata_identity_is_deterministic_without_self_hash():
    artifact = email_text_activity_artifact()
    document = json.loads(artifact)
    descriptor = email_text_activity_descriptor()
    assert artifact == canonical(document).encode("utf-8")
    assert artifact == email_text_activity_artifact()
    assert (
        descriptor.activity_ref.artifact_digest == "sha256:" + hashlib.sha256(artifact).hexdigest()
    )
    assert document["kind"] == "email-text-activity-contract"
    assert document["activity_identity"] == {"activity_id": "nexus.email.text", "version": "1.0.0"}
    assert document["adapter_contract_version"] == "1.0.0"
    assert "activity_ref" not in document["descriptor"]
    assert b'"artifact_digest"' not in artifact
    assert not {"files", "runtime", "source"} & document.keys()


def test_metadata_exposes_no_execution_or_delivery_claim():
    descriptor = email_text_activity_descriptor()
    assert descriptor.connection_requirements == ("email",)
    assert descriptor.runtime_requirements == ("connector.email.text.v1",)
    assert descriptor.execution_policy.maximum_attempts == 1
    assert descriptor.execution_policy.timeout_seconds == 30
    assert descriptor.activity_ref != http_json_activity_descriptor().activity_ref
    for text in ("no email is sent", "not delivery", "not Python code", "literal inputs"):
        assert text in descriptor.documentation
    catalog = EmailTextDescriptorCatalog()
    assert not any(hasattr(catalog, name) for name in ("execute", "resolve", "publish", "search"))


def test_all_schemas_are_portable_and_reference_free():
    descriptor = email_text_activity_descriptor()
    for schema in (
        descriptor.input_schema,
        descriptor.output_schema,
        descriptor.configuration_schema,
    ):
        Draft202012Validator.check_schema(schema)
        encoded = canonical(schema)
        assert not any(
            token in encoded for token in ('"$ref"', '"$defs"', '"$dynamicRef"', '"format"')
        )
    assert descriptor.input_schema["additionalProperties"] is False
    assert descriptor.configuration_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    "value",
    [
        message(),
        message(execution_mode="mock", fixture_digest=DIGEST),
        message(cc=["manager@example.test"], bcc=["audit@example.test"]),
        message(
            to=["First.Last+onboarding@EXAMPLE.TEST"],
            subject="Bienvenue é",
            text_body="一行\r\n二行",
        ),
        message(to=[f"person{i}@example.test" for i in range(50)]),
    ],
)
def test_supported_literal_authoring_examples_validate(value):
    assert list(validator().iter_errors(value)) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"to": []},
        {"to": ["person@example.test"] * 51},
        {"to": ["person@example.test", "person@example.test"]},
        {"to": ["Name <person@example.test>"]},
        {"to": ["person@example.test\n"]},
        {"to": ["person@example.test\r\nBcc: other@example.test"]},
        {"to": ["person..name@example.test"]},
        {"to": ["a" * 65 + "@example.test"]},
        {"to": ["person@" + "a" * 64 + ".test"]},
        {"to": ["person@localhost"]},
        {"to": ["person@[127.0.0.1]"]},
        {"to": ["雪@example.test"]},
        {"cc": [3]},
        {"subject": ""},
        {"subject": "  \t"},
        {"subject": "\u00a0"},
        {"subject": "private\n"},
        {"subject": "private\r\nBcc: other@example.test"},
        {"subject": "private\x85"},
        {"subject": "x" * 257},
        {"text_body": ""},
        {"text_body": "private\x00body"},
        {"execution_mode": "mock"},
        {"execution_mode": "mock", "fixture_digest": None},
        {"execution_mode": "mock", "fixture_digest": DIGEST + "\n"},
        {"execution_mode": "live", "fixture_digest": DIGEST},
        {"execution_mode": "send"},
        {"from": "sender@example.test"},
        {"headers": {"Bcc": "hidden@example.test"}},
        {"scope": {"organization_id": "foreign", "environment_id": "prod"}},
        {"operation_id": "caller-selected"},
        {"password": "private-value"},
    ],
)
def test_portable_schema_rejects_unsupported_or_injected_fields(changes):
    assert list(validator().iter_errors(message(**changes)))


def test_output_is_only_future_submission_metadata():
    schema = email_text_activity_descriptor().output_schema
    assert set(schema["properties"]) == {"operation_id", "execution_mode", "submission_status"}
    accepted = {
        "operation_id": "email-1",
        "execution_mode": "mock",
        "submission_status": "accepted",
    }
    checker = Draft202012Validator(schema)
    assert checker.is_valid(accepted)
    assert not checker.is_valid({**accepted, "submission_status": "delivered"})
    for name in ("to", "bcc", "text_body", "subject", "provider_response", "message_id"):
        assert not checker.is_valid({**accepted, name: "private"})


def test_synthetic_schema_examples_are_valid_and_independent():
    first = email_text_activity_descriptor()
    checker = Draft202012Validator(first.input_schema)
    assert {example["execution_mode"] for example in first.input_schema["examples"]} == {
        "live",
        "mock",
    }
    for example in first.input_schema["examples"]:
        assert checker.is_valid(example)
    first.input_schema["examples"][0]["to"].append("changed@example.test")
    assert email_text_activity_descriptor().input_schema["examples"][0]["to"] == [
        "employee@example.test"
    ]


def test_exact_catalog_lookup_returns_fresh_independent_schemas():
    catalog = EmailTextDescriptorCatalog()
    reference = email_text_activity_descriptor().activity_ref
    first = catalog.descriptor(reference)
    second = catalog.descriptor(reference)
    assert first == second and first is not second
    first.input_schema["properties"]["subject"]["maxLength"] = 1
    assert second.input_schema["properties"]["subject"]["maxLength"] == 256
    assert catalog.descriptor(reference).input_schema == second.input_schema
    assert catalog.descriptor(http_json_activity_descriptor().activity_ref) is None
    for field, value in (
        ("activity_id", "nexus.email.other"),
        ("version", "2.0.0"),
        ("artifact_digest", DIGEST),
    ):
        changed = deepcopy(reference.model_dump())
        changed[field] = value
        assert catalog.descriptor(ActivityRef.model_validate(changed)) is None


@pytest.mark.parametrize(
    "reference",
    [
        None,
        {},
        "private",
        ActivityRef.model_construct(activity_id=1, version="1", artifact_digest=DIGEST),
    ],
)
def test_catalog_malformed_lookup_fails_closed(reference):
    assert EmailTextDescriptorCatalog().descriptor(reference) is None
