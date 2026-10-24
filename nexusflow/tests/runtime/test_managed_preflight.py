"""Opt-in managed approvals compile without changing legacy proof plans."""

import json
from pathlib import Path

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan
from nexusflow.modules.durable_runtime.task_approval import parse_task_approval
from nexusflow.modules.durable_runtime.types import ReleaseBinding


def compile_configuration(configuration, *, node_index=1):
    definition = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "purchasing.workflow.json").read_text(
            encoding="utf-8"
        )
    )
    definition["nodes"][node_index]["input_mapping"] = {"human_task": configuration}
    return compile_plan(
        json.dumps(definition),
        ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        Scope(organization_id="acme", environment_id="dev"),
    )


def test_managed_configuration_is_canonical_and_pinned():
    config = {"user_ids": ["alice"], "rule": "any_one"}
    plan = compile_configuration(config)
    compiled = plan.steps[1].task_approval_json
    assert parse_task_approval(compiled).user_ids == ("alice",)
    assert (
        compiled
        == compile_configuration(dict(reversed(list(config.items())))).steps[1].task_approval_json
    )
    assert all(not step.task_approval_json for step in plan.steps if step.kind != "approval")
    config["user_ids"].append("bob")
    assert parse_task_approval(compiled).user_ids == ("alice",)


def test_sequential_configuration_preserves_approval_stages():
    plan = compile_configuration(
        {
            "rule": "sequential",
            "stages": [
                {"stage_id": "manager", "rule": "all_approvers", "group_ids": ["managers"]},
                {"stage_id": "finance", "rule": "any_one", "user_ids": ["financer"]},
            ],
        }
    )
    config = parse_task_approval(plan.steps[1].task_approval_json)
    assert tuple(stage.stage_id for stage in config.stages) == ("manager", "finance")
    assert config.stages[0].group_ids == ("managers",)


@pytest.mark.parametrize(
    "configuration",
    [
        None,
        [],
        {},
        {"rule": "any_one"},
        {"rule": "unknown", "user_ids": ["alice"]},
        {"rule": "any_one", "user_ids": ["alice"], "run_id": "forged"},
        {"rule": "any_one", "user_ids": ["alice"], "extra": True},
        {"rule": "any_one", "user_ids": "alice"},
        {"rule": "any_one", "user_ids": ["alice"], "stages": [{}]},
        {"rule": "sequential", "stages": []},
        {"rule": "sequential", "user_ids": ["alice"], "stages": []},
        {
            "rule": "sequential",
            "stages": [
                {"stage_id": "same", "rule": "any_one", "user_ids": ["alice"]},
                {"stage_id": "same", "rule": "any_one", "user_ids": ["bob"]},
            ],
        },
    ],
)
def test_invalid_managed_configuration_rejected(configuration):
    with pytest.raises(PlanError, match="[Hh]uman task"):
        compile_configuration(configuration)


@pytest.mark.parametrize("node_index", [0, 2, 3])
def test_reserved_configuration_only_allowed_on_approval(node_index):
    with pytest.raises(PlanError, match="requires an approval"):
        compile_configuration({"rule": "any_one", "user_ids": ["alice"]}, node_index=node_index)
