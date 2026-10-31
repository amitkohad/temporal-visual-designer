"""Pure checks for the opt-in mock-only Temporal activity bridge."""

import hashlib
import json
import re
from typing import Any

from pydantic import TypeAdapter

from nexusflow.contracts.models import Digest, Identifier, Scope
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.activity_types import HTTPJSONActivityInput
from nexusflow.modules.connectors.temporal_mock_types import (
    HTTPMockActivityContext,
    HTTPMockDispatchError,
    PreparedHTTPMockCall,
)
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding
from nexusflow.modules.project_versioning.canonical import canonical, digest


def _text(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value.strip() != value
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        raise ValueError
    return value


def _identifier(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Identifier).validate_python(value, strict=True)


def _digest(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Digest).validate_python(value, strict=True)


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope(
        organization_id=_identifier(value.organization_id),
        environment_id=_identifier(value.environment_id),
    )


def _object(encoded: str) -> dict[str, Any]:
    if type(encoded) is not str or len(encoded) > 65536 or len(encoded.encode("utf-8")) > 65536:
        raise ValueError

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject(_value: str) -> None:
        raise ValueError

    value = json.loads(encoded, object_pairs_hook=unique, parse_constant=reject)
    if type(value) is not dict:
        raise ValueError
    HTTPJSONActivityInput.bounded_body(value)
    return value


def prepare_http_mock_call(
    call: ActivityCall,
    context: HTTPMockActivityContext,
    scope: Scope,
    namespace: str,
    task_queue: str,
) -> PreparedHTTPMockCall:
    """Bind exact mock metadata to trusted worker/Temporal identity, without I/O."""
    try:
        selected_scope = _scope(scope)
        selected_namespace = _text(namespace)
        if re.fullmatch(r"[A-Za-z0-9_.-]+", selected_namespace) is None:
            raise ValueError
        selected_queue = _text(task_queue)
        if type(call) is not ActivityCall or type(context) is not HTTPMockActivityContext:
            raise ValueError
        if type(call.binding) is not ReleaseBinding:
            raise ValueError
        binding = call.binding
        bound_scope = Scope(
            organization_id=_identifier(binding.organization_id),
            environment_id=_identifier(binding.environment_id),
        )
        release_id = _identifier(binding.release_id)
        configuration_revision = _identifier(binding.configuration_revision)
        release_digest = _digest(binding.release_digest)
        node_id = _identifier(call.node_id)
        for value in (
            context.namespace,
            context.workflow_namespace,
            context.task_queue,
            context.workflow_type,
            context.workflow_id,
            context.workflow_run_id,
            context.activity_type,
            context.activity_id,
        ):
            _text(value)
        if type(context.attempt) is not int or not 1 <= context.attempt < 2**31:
            raise ValueError
        if any(
            type(value) is not str
            for value in (
                binding.runtime_version,
                call.operation_id,
                call.activity_id,
                call.activity_version,
                call.artifact_digest,
            )
        ):
            raise ValueError
        if (
            context.namespace != selected_namespace
            or context.workflow_namespace != selected_namespace
            or context.task_queue != selected_queue
            or context.workflow_type != "NexusFlowProofV1"
            or context.activity_type != "nexusflow.proof.dispatch"
            or context.activity_id != node_id
            or binding.runtime_version != "proof.v1"
            or bound_scope != selected_scope
            or call.operation_id != f"{context.workflow_id}:{node_id}"
        ):
            raise HTTPMockDispatchError("forbidden") from None
        reference = http_json_activity_descriptor().activity_ref
        if (call.activity_id, call.activity_version, call.artifact_digest) != (
            reference.activity_id,
            reference.version,
            reference.artifact_digest,
        ):
            raise HTTPMockDispatchError("forbidden") from None
        raw = _object(call.input_json)
        _object(call.workflow_input_json)
        inputs = HTTPJSONActivityInput.model_validate(raw, strict=True)
        if inputs.execution_mode != "mock":
            raise HTTPMockDispatchError("forbidden") from None
        # Re-encoding validated values creates a private independent JSON snapshot.
        input_document = {
            "connection_id": inputs.connection_id,
            "connection_revision": inputs.connection_revision,
            "execution_mode": inputs.execution_mode,
            "fixture_digest": inputs.fixture_digest,
            "method": inputs.method,
            "path": inputs.path,
            "json_body": inputs.json_body,
        }
        inputs = HTTPJSONActivityInput.model_validate_json(canonical(input_document), strict=True)
        identity = {
            "kind": "temporal-http-mock-operation",
            "schema_version": "0.1",
            "namespace": selected_namespace,
            "task_queue": selected_queue,
            "scope": {
                "organization_id": selected_scope.organization_id,
                "environment_id": selected_scope.environment_id,
            },
            "workflow_id": context.workflow_id,
            "workflow_run_id": context.workflow_run_id,
            "node_id": node_id,
            "release_id": release_id,
            "release_digest": release_digest,
            "configuration_revision": configuration_revision,
            "activity_reference": {
                "activity_id": reference.activity_id,
                "version": reference.version,
                "artifact_digest": reference.artifact_digest,
            },
            "input_digest": digest(input_document),
        }
        operation_id = "http-" + hashlib.sha256(canonical(identity).encode("utf-8")).hexdigest()
        return PreparedHTTPMockCall(selected_scope, operation_id, inputs)
    except HTTPMockDispatchError:
        raise
    except Exception:
        raise HTTPMockDispatchError("invalid_request") from None
