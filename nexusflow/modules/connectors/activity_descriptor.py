"""Content-addressed HTTP contract metadata, not an executable Python artifact."""

from typing import Any

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, DataSchema
from nexusflow.modules.connectors.activity_types import (
    HTTPJSONActivityInput,
    HTTPJSONActivityOutput,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest

_ACTIVITY_ID = "nexus.http.json"
_VERSION = "1.0.0"
_DOCUMENTATION = (
    "Configure an exact scoped HTTP connection revision, relative path and explicit live or mock "
    "mode. Mock mode requires a pinned fixture digest and trusted Dev authorization; live mode "
    "forbids a fixture digest. GET and HEAD have no JSON body. Other supported methods accept an "
    "optional JSON object encoded canonically as UTF-8, limited at execution to 1 MiB, depth 32 "
    "and 10,000 nodes. Scope and operation identity come from trusted host context, never input. "
    "HTTP statuses, including 4xx/5xx, are returned as results without automatic retry or status "
    "failure classification. Output contains only operation ID, status and selected execution "
    "mode; all raw response headers and body are omitted. Metadata identity describes this "
    "versioned contract, not Python code, an executable package or runtime capability. Current "
    "offline authoring supports literal inputs; general variable bindings, credential resolution, "
    "response mapping, Temporal registration and default API/palette integration remain separate. "
    "The portable schema describes field and mode combinations; backend validation also enforces "
    "full path rules and JSON structural/encoded-byte limits."
)


def _input_schema() -> DataSchema:
    schema = HTTPJSONActivityInput.model_json_schema()
    # Authoring's offline registry deliberately rejects even local recursive refs.
    # The backend validates this object's actual JSON tree and structural limits.
    schema.pop("$defs", None)
    schema["properties"]["json_body"] = {
        "title": "JSON body",
        "description": "Optional JSON object; backend enforces tree and encoded-byte limits.",
        "anyOf": [{"type": "object", "additionalProperties": {}}, {"type": "null"}],
        "default": None,
    }
    schema["properties"]["path"]["pattern"] = r"^/(?!/)"
    schema["allOf"] = [
        {
            "if": {
                "properties": {"execution_mode": {"const": "mock"}},
                "required": ["execution_mode"],
            },
            "then": {
                "required": ["fixture_digest"],
                "properties": {"fixture_digest": {"type": "string"}},
            },
            "else": {"properties": {"fixture_digest": {"type": "null"}}},
        },
        {
            # Missing method defaults to GET, so the condition also matches absence.
            "if": {"properties": {"method": {"enum": ["GET", "HEAD"]}}},
            "then": {"properties": {"json_body": {"type": "null"}}},
        },
    ]
    return schema


def _metadata_definition() -> dict[str, Any]:
    return {
        "kind": "http-json-activity-contract",
        "schema_version": "0.1",
        "adapter_contract_version": _VERSION,
        "activity_identity": {"activity_id": _ACTIVITY_ID, "version": _VERSION},
        "descriptor": {
            "descriptor_schema_version": "0.1",
            "display_name": "HTTP JSON request",
            "category": "connectors",
            "description": "Send one credential-free HTTP JSON request or explicitly match a Dev fixture.",
            "input_schema": _input_schema(),
            "output_schema": HTTPJSONActivityOutput.model_json_schema(),
            "configuration_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "connection_requirements": ["http"],
            "execution_policy": {"timeout_seconds": 30, "maximum_attempts": 1},
            "runtime_requirements": ["connector.http.json.v1"],
            "documentation": _DOCUMENTATION,
        },
    }


def http_json_activity_artifact() -> bytes:
    """Return canonical UTF-8 contract metadata; no source/executable is supplied."""
    return canonical(_metadata_definition()).encode("utf-8")


def http_json_activity_descriptor() -> ActivityDescriptor:
    """Construct fresh schemas and content identity without self-referential hashing."""
    metadata = _metadata_definition()
    value = metadata["descriptor"]
    value["activity_ref"] = {
        **metadata["activity_identity"],
        "artifact_digest": digest(metadata),
    }
    return ActivityDescriptor.model_validate_json(canonical(value))


class HTTPJSONDescriptorCatalog:
    """Descriptor-only lookup: intentionally no executable artifact resolver."""

    def descriptor(self, reference: ActivityRef) -> ActivityDescriptor | None:
        try:
            if type(reference) is not ActivityRef or any(
                type(value) is not str
                for value in (reference.activity_id, reference.version, reference.artifact_digest)
            ):
                return None
            selected = ActivityRef.model_validate(
                {
                    "activity_id": reference.activity_id,
                    "version": reference.version,
                    "artifact_digest": reference.artifact_digest,
                },
                strict=True,
            )
            result = http_json_activity_descriptor()
            return result if selected == result.activity_ref else None
        except Exception:
            return None
