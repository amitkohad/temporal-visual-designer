"""Pinned activity schemas and conservative output privacy at the service boundary."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nexusflow.contracts.models import ActivityDescriptor
from nexusflow.modules.activity_catalog.catalog_types import CatalogEntry
from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.types import ActivityManifest, BundleFile, PythonRuntime
from nexusflow.modules.designer_debugger.inspection import inspect_values
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import DebugStepOutput, Snapshot
from nexusflow.tests.debugger.test_sessions import setup  # noqa: F401


def public(kind="string", **extra):
    return {"type": kind, "x-nexusflow-inspect": True, **extra}


@pytest.fixture
def output_case(setup):  # noqa: F811
    scope, service, request, repository, auth, runtime, readiness = setup

    async def create(properties=None, raw='{"public":"visible","private":"secret"}'):
        descriptor = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "activity-descriptor.json").read_text()
        )
        descriptor["connection_requirements"] = []
        descriptor["output_schema"] = {
            "type": "object",
            "properties": {"public": public(), "private": {"type": "string"}}
            if properties is None
            else properties,
        }
        manifest = ActivityManifest(
            schema_version="0.1",
            descriptor=ActivityDescriptor.model_validate_json(json.dumps(descriptor)),
            runtime=PythonRuntime(
                language="python", python_version="3.12", entrypoint="activity:run"
            ),
            files=(
                BundleFile(
                    path="src/activity.py",
                    digest="sha256:" + hashlib.sha256(b"pass").hexdigest(),
                    size=4,
                ),
            ),
        )
        reference = manifest.descriptor.activity_ref.model_copy(
            update={"artifact_digest": bundle_digest(manifest)}
        )
        manifest = manifest.model_copy(
            update={
                "descriptor": manifest.descriptor.model_copy(update={"activity_ref": reference})
            }
        )
        content = json.loads(service.projects.revision(scope, "purchase", 1).content_json)
        content["activity_refs"] = [reference.model_dump(mode="json")]
        workflow = content["workflows"][0]
        workflow["nodes"].insert(
            1,
            {
                "node_id": "work",
                "kind": "activity",
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
                "input_mapping": {"message": "test"},
                "activity_ref": reference.model_dump(mode="json"),
            },
        )
        workflow["edges"] = [
            {
                "source_node_id": source,
                "source_port": "out",
                "target_node_id": target,
                "target_port": "in",
            }
            for source, target in (("start", "work"), ("work", "finish"))
        ]
        service.projects.save(scope, "purchase", 1, json.dumps(content), "Activity", "alice")
        session = await service.start(
            scope, "purchase", request.model_copy(update={"source_revision": 2})
        )
        entry = CatalogEntry(
            scope,
            reference,
            manifest.model_dump_json(),
            "published",
            1,
            "publisher",
            "now",
            "publisher",
            "now",
        )
        catalog = SimpleNamespace(get=Mock(return_value=entry))
        service.catalog = catalog
        snapshot = Snapshot(
            session.session_id,
            session.definition_digest,
            "proof.v1",
            "finish",
            "paused",
            1,
            ("start", "work"),
        )
        runtime.step_output = AsyncMock(return_value=DebugStepOutput(snapshot, "work", raw))
        return SimpleNamespace(
            scope=scope,
            service=service,
            session=session,
            runtime=runtime,
            catalog=catalog,
            entry=entry,
            manifest=manifest,
            snapshot=snapshot,
            auth=auth,
            repository=repository,
            readiness=readiness,
        )

    return create


async def inspect(case, node="work"):
    return await case.service.inspect_output(case.scope, "purchase", case.session.session_id, node)


@pytest.mark.asyncio
async def test_only_pinned_public_output_is_returned_without_control_or_readiness(output_case):
    case = await output_case()
    previous = case.repository.get(case.scope, "purchase", case.session.session_id)
    result = await inspect(case)
    assert result.values == {"public": "visible"}
    assert result.redacted_paths == ("/private",)
    assert "secret" not in result.model_dump_json()
    assert result.activity_ref == case.entry.reference and result.node_id == "work"
    case.runtime.step_output.assert_awaited_once_with(
        case.service.placement, case.session.execution_id, "run-1", "work"
    )
    assert case.runtime.controls == [] and case.readiness.calls == 1
    assert case.repository.get(case.scope, "purchase", case.session.session_id) == previous


@pytest.mark.asyncio
async def test_protected_fields_unknown_names_and_unsupported_schema_stay_hidden(output_case):
    case = await output_case(
        {
            "safe": public(),
            "password": public(format="password"),
            "token": public(**{"x-secret": True}),
            "write": public(writeOnly=True),
            "union": {"anyOf": [public(), {"type": "null"}]},
        },
        json.dumps(
            {
                "safe": "shown",
                "password": "hidden1",
                "token": "hidden2",
                "write": "hidden3",
                "union": "hidden4",
                "dynamic-secret-name": "hidden5",
            }
        ),
    )
    result = await inspect(case)
    assert result.values == {"safe": "shown"}
    assert "dynamic-secret-name" not in result.model_dump_json()
    assert not any("hidden" in str(value) for value in result.model_dump().values())


@pytest.mark.asyncio
async def test_array_opt_ins_and_position_preserving_projection(output_case):
    case = await output_case(
        {
            "items": public("array", **{"x-nexusflow-inspect-array": True, "items": public()}),
            "legacy": public("array", items=public()),
        },
        json.dumps({"items": ["ok", "x" * 1100, "last"], "legacy": ["hidden"]}),
    )
    result = await inspect(case)
    assert result.values == {"items": ["ok", None, "last"]}
    assert result.redacted_paths == ("/items/1", "/legacy") and result.truncated


@pytest.mark.asyncio
async def test_public_type_mismatch_is_masked(output_case):
    case = await output_case({"value": public("integer")}, '{"value":"private-wrong-type"}')
    result = await inspect(case)
    assert result.values == {} and result.redacted_paths == ("/value",)


@pytest.mark.asyncio
async def test_whole_response_budget_includes_activity_metadata(output_case):
    properties = {f"field-{index}": public() for index in range(26)}
    case = await output_case(properties, json.dumps({name: "v" * 600 for name in properties}))
    record = case.repository.get(case.scope, "purchase", case.session.session_id)
    projection = inspect_values(
        record,
        {name: "v" * 600 for name in properties},
        case.manifest.descriptor.output_schema,
    )
    assert projection.values
    assert len(projection.model_dump_json().encode()) <= 16384
    result = await inspect(case)
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated
    assert len(result.model_dump_json().encode()) <= 16384


@pytest.mark.asyncio
async def test_protected_parent_hides_public_descendants(output_case):
    case = await output_case(
        {
            "parent": public("object", **{"x-sensitive": True, "properties": {"leaf": public()}}),
            "public": public(),
        },
        '{"parent":{"leaf":"hidden-descendant"},"public":"shown"}',
    )
    result = await inspect(case)
    assert result.values == {"public": "shown"}
    assert result.redacted_paths == ("/parent",)
    assert "hidden-descendant" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        '{"secret":NaN}',
        '{"secret":Infinity}',
        '{"secret":1e999}',
        '{"a":1,"a":2}',
        '{"a":{"b":1,"b":2}}',
        "[]",
        '"secret"',
        "not-json",
        '{"a":"' + "s" * 65536 + '"}',
    ],
    ids=[
        "nan",
        "infinity",
        "overflow",
        "duplicate",
        "nested-duplicate",
        "array",
        "scalar",
        "invalid",
        "oversize",
    ],
)
async def test_malformed_or_oversized_outputs_fail_safely(output_case, raw):
    case = await output_case(raw=raw)
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable" and "secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["release", "digest", "version", "node", "completed", "type"])
async def test_runtime_output_binding_mismatch_is_unavailable(output_case, change):
    case = await output_case()
    snapshot, node, raw = case.snapshot, "work", "{}"
    if change == "release":
        snapshot = replace(snapshot, release_id="other")
    if change == "digest":
        snapshot = replace(snapshot, definition_digest="sha256:" + "a" * 64)
    if change == "version":
        snapshot = replace(snapshot, runtime_version="other")
    if change == "node":
        node = "other"
    if change == "completed":
        snapshot = replace(snapshot, completed_nodes=("start",))
    case.runtime.step_output.return_value = (
        object() if change == "type" else DebugStepOutput(snapshot, node, raw)
    )
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["scope", "reference", "descriptor-reference", "schema", "oversize", "missing"]
)
async def test_catalog_binding_and_schema_digest_fail_closed_before_runtime(output_case, change):
    case = await output_case()
    entry = case.entry
    if change == "scope":
        entry = replace(entry, scope=case.scope.model_copy(update={"organization_id": "other"}))
    if change == "reference":
        entry = replace(entry, reference=entry.reference.model_copy(update={"version": "2.0.0"}))
    if change in {"schema", "descriptor-reference"}:
        value = json.loads(entry.manifest_json)
        if change == "schema":
            value["descriptor"]["output_schema"]["properties"]["private"]["x-nexusflow-inspect"] = (
                True
            )
        else:
            value["descriptor"]["activity_ref"]["version"] = "2.0.0"
        entry = replace(entry, manifest_json=json.dumps(value))
    if change == "oversize":
        entry = replace(entry, manifest_json="s" * 262145)
    if change == "missing":
        case.service.catalog = None
    case.catalog.get.return_value = entry
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"
    case.runtime.step_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_withdrawn_pinned_schema_remains_readable(output_case):
    case = await output_case()
    case.catalog.get.return_value = replace(case.entry, state="withdrawn")
    assert (await inspect(case)).values == {"public": "visible"}


@pytest.mark.asyncio
async def test_legacy_runtime_without_output_port_is_safely_unavailable(output_case):
    case = await output_case()
    del case.runtime.step_output
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_fresh_owner_and_authorization_before_catalog_or_runtime(output_case):
    case = await output_case()
    case.auth.actor = "other"
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "forbidden"
    case.auth.actor, case.auth.denied = "alice", True
    with pytest.raises(PermissionError):
        await inspect(case)
    case.catalog.get.assert_not_called()
    case.runtime.step_output.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node,code",
    [("missing", "not_found"), ("start", "conflict"), ("../private", "invalid_request")],
)
async def test_unknown_or_nonactivity_nodes_do_not_query_runtime(output_case, node, code):
    case = await output_case()
    with pytest.raises(DebugError) as error:
        await inspect(case, node)
    assert error.value.code == code
    case.catalog.get.assert_not_called()
    case.runtime.step_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_source_digest_not_latest_revision_controls_inspection(output_case):
    case = await output_case()
    source = case.service.projects.revision(case.scope, "purchase", 2)
    value = json.loads(source.content_json)
    value["workflows"][0]["output_schema"] = {"type": "object", "properties": {"private": public()}}
    case.service.projects.save(case.scope, "purchase", 2, json.dumps(value), "Later edit", "alice")
    assert (await inspect(case)).values == {"public": "visible"}
    case.service.projects.revision = Mock(
        return_value=replace(source, content_json=json.dumps(value))
    )
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"
