"""Frozen literal activity inputs use pinned schemas without runtime access."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import ActivityDescriptor
from nexusflow.modules.activity_catalog.catalog_types import CatalogEntry
from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.types import ActivityManifest, BundleFile, PythonRuntime
from nexusflow.modules.designer_debugger.inspection import inspect_values
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.tests.debugger.test_sessions import setup  # noqa: F401


def public(kind="string", **extra):
    return {"type": kind, "x-nexusflow-inspect": True, **extra}


@pytest.fixture
def input_case(setup):  # noqa: F811
    scope, service, request, repository, auth, runtime, readiness = setup

    async def create(properties=None, values=None):
        values = {"public": "visible", "private": "secret"} if values is None else values
        descriptor = json.loads(
            (Path(__file__).parents[1] / "fixtures/activity-descriptor.json").read_text()
        )
        descriptor["connection_requirements"] = []
        descriptor["input_schema"] = {
            "type": "object",
            "properties": {"public": public(), "private": {"type": "string"}}
            if properties is None
            else properties,
        }
        # Deliberately public output schema cannot declassify an input.
        descriptor["output_schema"] = {"type": "object", "properties": {"private": public()}}
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
                "input_mapping": values,
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
            scope,
            "purchase",
            request.model_copy(
                update={"source_revision": 2, "input": {"public": "workflow-value-must-not-merge"}}
            ),
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
        # Inspection must not even access a runtime/readiness method.
        service.runtime = object()
        service.readiness = object()
        return SimpleNamespace(
            scope=scope,
            service=service,
            session=session,
            repository=repository,
            catalog=catalog,
            entry=entry,
            manifest=manifest,
            auth=auth,
            readiness=readiness,
            runtime=runtime,
        )

    return create


async def inspect(case, node="work"):
    return await case.service.inspect_step_input(
        case.scope, "purchase", case.session.session_id, node
    )


def change_plan(case, **changes):
    record = case.repository.get(case.scope, "purchase", case.session.session_id)
    steps = tuple(
        replace(step, **changes) if step.node_id == "work" else step for step in record.plan.steps
    )
    altered = replace(record, plan=replace(record.plan, steps=steps))
    case.repository.get = Mock(return_value=altered)


@pytest.mark.asyncio
async def test_literal_input_before_execution_uses_no_runtime_or_readiness(input_case):
    case = await input_case()
    original = case.repository.get(case.scope, "purchase", case.session.session_id)
    result = await inspect(case)
    assert result.values == {"public": "visible"}
    assert result.redacted_paths == ("/private",)
    assert result.activity_ref == case.entry.reference and result.node_id == "work"
    assert "secret" not in result.model_dump_json()
    assert "workflow-value-must-not-merge" not in result.model_dump_json()
    assert case.repository.get(case.scope, "purchase", case.session.session_id) == original
    assert len(case.runtime.starts) == 1 and case.runtime.controls == []
    assert case.readiness.calls == 1


@pytest.mark.asyncio
async def test_protected_unknown_and_unsupported_input_values_hidden(input_case):
    case = await input_case(
        {
            "safe": public(),
            "credential": public(format="password"),
            "token": public(**{"x-secret": True}),
            "write": public(writeOnly=True),
            "union": {"anyOf": [public(), {"type": "null"}]},
            "parent": public("object", **{"x-sensitive": True, "properties": {"leaf": public()}}),
        },
        {
            "safe": "shown",
            "credential": "hidden1",
            "token": "hidden2",
            "write": "hidden3",
            "union": "hidden4",
            "dynamic-secret-name": "hidden5",
            "parent": {"leaf": "hidden6"},
        },
    )
    result = await inspect(case)
    assert result.values == {"safe": "shown"}
    assert "dynamic-secret-name" not in result.model_dump_json()
    assert "hidden" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_array_opt_ins_and_indices_preserved(input_case):
    case = await input_case(
        {
            "items": public("array", **{"x-nexusflow-inspect-array": True, "items": public()}),
            "legacy": public("array", items=public()),
        },
        {"items": ["ok", "x" * 1100, "last"], "legacy": ["hidden"]},
    )
    result = await inspect(case)
    assert result.values == {"items": ["ok", None, "last"]}
    assert result.redacted_paths == ("/items/1", "/legacy") and result.truncated


@pytest.mark.asyncio
async def test_type_mismatch_and_array_tail_are_bounded(input_case):
    case = await input_case(
        {
            "wrong": public("integer"),
            "items": public("array", **{"x-nexusflow-inspect-array": True, "items": public()}),
        },
        {"wrong": "hidden", "items": [str(index) for index in range(101)]},
    )
    result = await inspect(case)
    assert "wrong" not in result.values and len(result.values["items"]) == 100
    assert set(result.redacted_paths) == {"/wrong", "/items"} and result.truncated


@pytest.mark.asyncio
async def test_complete_response_budget_includes_activity_metadata(input_case):
    properties = {f"field-{index}": public() for index in range(26)}
    values = {name: "v" * 600 for name in properties}
    case = await input_case(properties, values)
    record = case.repository.get(case.scope, "purchase", case.session.session_id)
    assert inspect_values(record, values, case.manifest.descriptor.input_schema).values
    result = await inspect(case)
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated
    assert len(result.model_dump_json().encode()) <= 16384


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
async def test_malformed_or_oversized_plan_input_is_safe(input_case, raw):
    case = await input_case()
    change_plan(case, input_json=raw)
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable" and "secret" not in str(error.value)
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"input_json": '{"public":"different"}'},
        {"kind": "wait"},
        {"activity_id": "other"},
        {"activity_version": "2.0.0"},
        {"artifact_digest": "sha256:" + "a" * 64},
    ],
)
async def test_plan_binding_must_match_source(input_case, changes):
    case = await input_case()
    change_plan(case, **changes)
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["scope", "reference", "descriptor-reference", "schema", "oversize", "missing"]
)
async def test_catalog_binding_schema_digest_before_projection(input_case, change):
    case = await input_case()
    entry = case.entry
    if change == "scope":
        entry = replace(entry, scope=case.scope.model_copy(update={"organization_id": "other"}))
    if change == "reference":
        entry = replace(entry, reference=entry.reference.model_copy(update={"version": "2.0.0"}))
    if change in {"schema", "descriptor-reference"}:
        value = json.loads(entry.manifest_json)
        if change == "schema":
            value["descriptor"]["input_schema"]["properties"]["private"]["x-nexusflow-inspect"] = (
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


@pytest.mark.asyncio
async def test_withdrawn_pinned_schema_readable(input_case):
    case = await input_case()
    case.catalog.get.return_value = replace(case.entry, state="withdrawn")
    assert (await inspect(case)).values == {"public": "visible"}


@pytest.mark.asyncio
async def test_owner_permission_and_placement_are_fresh(input_case):
    case = await input_case()
    case.auth.actor = "other"
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "forbidden"
    case.auth.actor, case.auth.denied = "alice", True
    with pytest.raises(PermissionError):
        await inspect(case)
    case.auth.denied = False
    case.service.placement = replace(case.service.placement, task_queue="moved")
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "conflict"
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_non_dev_denied_before_store_or_catalog(input_case, environment):
    case = await input_case()
    case.repository.get = Mock(side_effect=AssertionError("No storage access"))
    with pytest.raises(DebugError) as error:
        await case.service.inspect_step_input(
            case.scope.model_copy(update={"environment_id": environment}),
            "purchase",
            case.session.session_id,
            "work",
        )
    assert error.value.code == "forbidden"
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node,code",
    [("missing", "not_found"), ("start", "conflict"), ("../private", "invalid_request")],
)
async def test_unknown_nonactivity_and_invalid_nodes(input_case, node, code):
    case = await input_case()
    with pytest.raises(DebugError) as error:
        await inspect(case, node)
    assert error.value.code == code
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
async def test_original_source_not_later_edits_and_corruption_rejected(input_case):
    case = await input_case()
    source = case.service.projects.revision(case.scope, "purchase", 2)
    value = json.loads(source.content_json)
    value["workflows"][0]["nodes"][1]["input_mapping"]["public"] = "changed"
    case.service.projects.save(case.scope, "purchase", 2, json.dumps(value), "Later", "alice")
    assert (await inspect(case)).values == {"public": "visible"}
    case.service.projects.revision = Mock(
        return_value=replace(source, content_json=json.dumps(value))
    )
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_plan_missing_activity_step_is_unavailable(input_case):
    case = await input_case()
    record = case.repository.get(case.scope, "purchase", case.session.session_id)
    changed = replace(
        record,
        plan=replace(
            record.plan, steps=tuple(step for step in record.plan.steps if step.node_id != "work")
        ),
    )
    case.repository.get = Mock(return_value=changed)
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "unavailable"
    case.catalog.get.assert_not_called()


@pytest.mark.asyncio
async def test_input_comparison_uses_values_not_json_whitespace(input_case):
    case = await input_case()
    change_plan(case, input_json=json.dumps({"private": "secret", "public": "visible"}, indent=2))
    assert (await inspect(case)).values == {"public": "visible"}


@pytest.mark.asyncio
async def test_unacknowledged_session_is_not_inspectable(input_case):
    case = await input_case()
    record = case.repository.get(case.scope, "purchase", case.session.session_id)
    changed = replace(
        record,
        session=record.session.model_copy(
            update={"state": "dispatch_pending", "temporal_run_id": None}
        ),
    )
    case.repository.get = Mock(return_value=changed)
    with pytest.raises(DebugError) as error:
        await inspect(case)
    assert error.value.code == "conflict"
    case.catalog.get.assert_not_called()
