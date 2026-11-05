"""Content-addressed email authoring metadata; no message delivery capability."""

from typing import Any

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, DataSchema
from nexusflow.modules.connectors.email_types import (
    EMAIL_ADDRESS_PATTERN,
    EmailTextActivityInput,
    EmailTextActivityOutput,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest

_ACTIVITY_ID = "nexus.email.text"
_VERSION = "1.0.0"
_DOCUMENTATION = (
    "Author a plain-text email using an exact scoped email connection revision and explicit live "
    "or mock mode. Mock authoring requires a fixture digest; live authoring forbids one. This "
    "increment supplies contract metadata only: no email is sent, no live or mock provider is "
    "implemented, and no default runtime/API/palette or executable publication is registered. "
    "The capability connector.email.text.v1 is deliberately unregistered. Sender identity, "
    "credentials, scope and operation identity belong to trusted host configuration, never "
    "business inputs. Recipients, subject and body are private message data. Recipient syntax "
    "is an explicit ASCII dot-atom mailbox plus multi-label DNS-style domain subset, not full "
    "email syntax: no display names, comments, quoted local parts, Unicode mailboxes or domain "
    "literals. Domains are lowercased, local spelling is preserved, and exact canonical duplicates "
    "across To/Cc/Bcc are rejected. At least one To recipient and at most 50 total recipients are "
    "allowed. Address/local/domain-label bounds are 254/64/63 characters. Subject is nonblank, "
    "1..256 characters and at most 1024 UTF-8 bytes, with no C0/C1 controls. Plain-text body is "
    "1..1048576 characters and at most 1 MiB UTF-8 with no NUL; ordinary body newlines are allowed. "
    "Portable schemas describe fields, syntax and mode combinations; backend validation remains "
    "authoritative for canonicalization, aggregate recipient rules and encoded-byte limits. "
    "Current offline authoring supports literal inputs. Output describes a future provider's "
    "submission acceptance, not delivery: accepted is not a delivered receipt. Raw provider "
    "responses, message content and recipient lists are not output fields. Transport, email "
    "connections, sender/recipient policy, secrets, mocks, durable delivery ambiguity and retry "
    "recovery, worker dispatch and general bindings remain separate work. Metadata identity "
    "describes this versioned contract, not Python code or an executable artifact."
)


def _input_schema() -> DataSchema:
    schema = EmailTextActivityInput.model_json_schema()
    for branch in schema["properties"]["fixture_digest"]["anyOf"]:
        if branch.get("type") == "string":
            branch["pattern"] = r"^sha256:[a-f0-9]{64}$(?![\s\S])"
    for name in ("to", "cc", "bcc"):
        schema["properties"][name]["items"].update(
            {"type": "string", "minLength": 3, "maxLength": 254, "pattern": EMAIL_ADDRESS_PATTERN}
        )
        schema["properties"][name]["maxItems"] = 50
        schema["properties"][name]["uniqueItems"] = True
    schema["properties"]["to"]["minItems"] = 1
    schema["properties"]["subject"].update(
        {
            "minLength": 1,
            "maxLength": 256,
            # Absolute end: unlike $, this cannot ignore a final newline.
            "pattern": r"^[^\u0000-\u001f\u007f-\u009f]+(?![\s\S])",
            "not": {"pattern": r"^\s*(?![\s\S])"},
        }
    )
    schema["properties"]["text_body"].update(
        {"minLength": 1, "maxLength": 1048576, "not": {"pattern": "\u0000"}}
    )
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
        }
    ]
    schema["examples"] = [
        {
            "connection_id": "employee-mail",
            "connection_revision": "v1",
            "execution_mode": "live",
            "to": ["employee@example.test"],
            "subject": "Welcome",
            "text_body": "Your onboarding checklist is ready.",
        },
        {
            "connection_id": "employee-mail",
            "connection_revision": "fixture-v1",
            "execution_mode": "mock",
            "fixture_digest": "sha256:" + "a" * 64,
            "to": ["employee@example.test"],
            "cc": ["manager@example.test"],
            "subject": "Approval request",
            "text_body": "Please review this synthetic request.",
        },
    ]
    return schema


def _metadata_definition() -> dict[str, Any]:
    return {
        "kind": "email-text-activity-contract",
        "schema_version": "0.1",
        "adapter_contract_version": _VERSION,
        "activity_identity": {"activity_id": _ACTIVITY_ID, "version": _VERSION},
        "descriptor": {
            "descriptor_schema_version": "0.1",
            "display_name": "Email text message",
            "category": "connectors",
            "description": "Author a bounded plain-text email contract; sending is not implemented.",
            "input_schema": _input_schema(),
            "output_schema": EmailTextActivityOutput.model_json_schema(),
            "configuration_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "connection_requirements": ["email"],
            "execution_policy": {"timeout_seconds": 30, "maximum_attempts": 1},
            "runtime_requirements": ["connector.email.text.v1"],
            "documentation": _DOCUMENTATION,
        },
    }


def email_text_activity_artifact() -> bytes:
    """Return canonical UTF-8 metadata without executable source or package files."""
    return canonical(_metadata_definition()).encode("utf-8")


def email_text_activity_descriptor() -> ActivityDescriptor:
    """Return fresh descriptor/schema objects with exact contract-content identity."""
    metadata = _metadata_definition()
    descriptor = metadata["descriptor"]
    descriptor["activity_ref"] = {
        **metadata["activity_identity"],
        "artifact_digest": digest(metadata),
    }
    return ActivityDescriptor.model_validate_json(canonical(descriptor))


class EmailTextDescriptorCatalog:
    """Exact metadata lookup only; intentionally no executable artifact resolver."""

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
            result = email_text_activity_descriptor()
            return result if selected == result.activity_ref else None
        except Exception:
            return None
