"""Offline HTTP contract metadata/schema tests; no artifact execution capability."""

import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from nexusflow.contracts.models import ActivityRef
from nexusflow.modules.connectors import activity_descriptor as module
from nexusflow.modules.connectors.activity_descriptor import (
    HTTPJSONDescriptorCatalog,
    http_json_activity_artifact,
    http_json_activity_descriptor,
)
from nexusflow.modules.project_versioning.canonical import canonical

DIGEST = "sha256:" + "a" * 64


def live(**changes):
    value = {
        "connection_id": "hr-api",
        "connection_revision": "v1",
        "execution_mode": "live",
        "method": "GET",
        "path": "/employees",
    }
    value.update(changes)
    return value


def test_metadata_identity_hashes_actual_canonical_bytes_without_self_reference():
    artifact = http_json_activity_artifact()
    document = json.loads(artifact)
    descriptor = http_json_activity_descriptor()
    assert artifact == canonical(document).encode("utf-8")
    assert (
        descriptor.activity_ref.artifact_digest == "sha256:" + hashlib.sha256(artifact).hexdigest()
    )
    assert artifact == http_json_activity_artifact()
    assert document["kind"] == "http-json-activity-contract"
    assert document["activity_identity"] == {"activity_id": "nexus.http.json", "version": "1.0.0"}
    assert document["adapter_contract_version"] == "1.0.0"
    assert "activity_ref" not in document["descriptor"]
    assert "files" not in document and "runtime" not in document
    assert b'"artifact_digest"' not in artifact


def test_descriptor_is_metadata_only_and_declares_capability_and_nonretry_policy():
    descriptor = http_json_activity_descriptor()
    assert descriptor.display_name == "HTTP JSON request" and descriptor.category == "connectors"
    assert descriptor.connection_requirements == ("http",)
    assert descriptor.runtime_requirements == ("connector.http.json.v1",)
    assert descriptor.execution_policy.timeout_seconds == 30
    assert descriptor.execution_policy.maximum_attempts == 1
    assert "not Python code" in descriptor.documentation
    assert "4xx/5xx" in descriptor.documentation
    assert "depth 32" in descriptor.documentation and "10,000 nodes" in descriptor.documentation
    assert "literal inputs" in descriptor.documentation
    catalog = HTTPJSONDescriptorCatalog()
    assert not any(hasattr(catalog, name) for name in ("resolve", "search", "publish", "execute"))


def test_schemas_are_offline_and_never_require_remote_or_local_reference_resolution():
    descriptor = http_json_activity_descriptor()
    for schema in (
        descriptor.input_schema,
        descriptor.output_schema,
        descriptor.configuration_schema,
    ):
        Draft202012Validator.check_schema(schema)
        encoded = canonical(schema)
        assert (
            '"$ref"' not in encoded and '"$dynamicRef"' not in encoded and '"$defs"' not in encoded
        )


@pytest.mark.parametrize(
    "value",
    [
        live(),
        live(method="HEAD"),
        live(method="POST", json_body={"employee": "example", "nested": [1, True, None]}),
        live(method="PUT", json_body={}),
        live(method="PATCH", json_body={}),
        live(method="DELETE", json_body=None),
        live(method="OPTIONS", json_body={}),
        live(execution_mode="mock", fixture_digest=DIGEST),
        live(execution_mode="mock", fixture_digest=DIGEST, method="POST", json_body={}),
        live(fixture_digest=None),
    ],
)
def test_valid_live_and_explicit_mock_inputs_match_portable_schema(value):
    assert (
        list(Draft202012Validator(http_json_activity_descriptor().input_schema).iter_errors(value))
        == []
    )


@pytest.mark.parametrize(
    "value",
    [
        live(execution_mode="mock"),
        live(execution_mode="mock", fixture_digest=None),
        live(execution_mode="mock", fixture_digest="invalid"),
        live(fixture_digest=DIGEST),
        live(execution_mode="auto"),
        live(method="GET", json_body={}),
        live(method="HEAD", json_body={}),
        live(method="post"),
        live(method="CONNECT"),
        live(method="POST", json_body=[]),
        live(method="POST", json_body="private-body"),
        live(path="https://other.example.com/"),
        live(path="//other.example.com/"),
        live(path=""),
        live(scope={"organization_id": "other", "environment_id": "prod"}),
        live(operation_id="caller-selected-operation"),
        live(headers={"authorization": "private-secret"}),
        live(secret_reference="private-secret"),
    ],
)
def test_invalid_combinations_and_untrusted_context_fields_are_schema_rejected(value):
    assert list(
        Draft202012Validator(http_json_activity_descriptor().input_schema).iter_errors(value)
    )


def test_omitted_method_uses_get_semantics_for_body_restriction():
    value = live(json_body={})
    value.pop("method")
    assert list(
        Draft202012Validator(http_json_activity_descriptor().input_schema).iter_errors(value)
    )
    value.pop("json_body")
    assert (
        list(Draft202012Validator(http_json_activity_descriptor().input_schema).iter_errors(value))
        == []
    )


def test_configuration_is_empty_and_all_execution_fields_are_business_inputs():
    descriptor = http_json_activity_descriptor()
    validator = Draft202012Validator(descriptor.configuration_schema)
    assert list(validator.iter_errors({})) == []
    assert list(validator.iter_errors({"connection_id": "hr-api"}))
    assert {"connection_id", "connection_revision", "execution_mode", "path"} <= set(
        descriptor.input_schema["required"]
    )


def test_output_omits_every_raw_response_field():
    schema = http_json_activity_descriptor().output_schema
    assert set(schema["properties"]) == {"operation_id", "status_code", "execution_mode"}
    validator = Draft202012Validator(schema)
    valid = {"operation_id": "run-step-1", "status_code": 503, "execution_mode": "live"}
    assert list(validator.iter_errors(valid)) == []
    for field in ("headers", "body", "json_body", "connection_id", "secret_reference"):
        assert list(validator.iter_errors({**valid, field: "private-provider-value"}))


def test_descriptors_and_catalog_reads_have_fresh_nested_schemas():
    first = http_json_activity_descriptor()
    reference = first.activity_ref
    first.input_schema["properties"]["method"]["enum"].append("CONNECT")
    first.output_schema["properties"]["body"] = {"type": "string"}
    second = HTTPJSONDescriptorCatalog().descriptor(reference)
    assert second is not None
    assert "CONNECT" not in second.input_schema["properties"]["method"]["enum"]
    assert "body" not in second.output_schema["properties"]
    second.configuration_schema["additionalProperties"] = True
    assert (
        HTTPJSONDescriptorCatalog()
        .descriptor(reference)
        .configuration_schema["additionalProperties"]
        is False
    )


@pytest.mark.parametrize(
    "field,value", [("activity_id", "other"), ("version", "2.0.0"), ("artifact_digest", DIGEST)]
)
def test_catalog_requires_exact_identity_version_and_digest(field, value):
    values = http_json_activity_descriptor().activity_ref.model_dump()
    values[field] = value
    assert HTTPJSONDescriptorCatalog().descriptor(ActivityRef(**values)) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "private-ref",
        False,
        ActivityRef.model_construct(activity_id=True, version="1.0.0", artifact_digest=DIGEST),
    ],
)
def test_catalog_malformed_references_fail_closed_without_serialization(value, recwarn):
    assert HTTPJSONDescriptorCatalog().descriptor(value) is None
    assert not recwarn


def test_metadata_change_changes_pin_and_old_pin_no_longer_resolves(monkeypatch):
    previous = http_json_activity_descriptor()
    modified = deepcopy(module._metadata_definition())
    modified["descriptor"]["display_name"] = "Changed contract metadata"
    monkeypatch.setattr(module, "_metadata_definition", lambda: deepcopy(modified))
    current = http_json_activity_descriptor()
    assert previous.activity_ref.artifact_digest != current.activity_ref.artifact_digest
    assert (
        current.activity_ref.artifact_digest
        == "sha256:" + hashlib.sha256(http_json_activity_artifact()).hexdigest()
    )
    assert HTTPJSONDescriptorCatalog().descriptor(previous.activity_ref) is None
    assert (
        HTTPJSONDescriptorCatalog().descriptor(current.activity_ref).display_name
        == "Changed contract metadata"
    )
