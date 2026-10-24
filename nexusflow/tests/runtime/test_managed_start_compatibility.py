"""An additive managed-task field must preserve prior permanent start receipts."""

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from nexusflow.modules.durable_runtime.starts import StartError
from nexusflow.modules.durable_runtime.types import Step
from nexusflow.tests.runtime.test_temporal_starts import setup as setup


@pytest.mark.asyncio
async def test_pre_upgrade_dispatch_receipt_still_acknowledges_manual_plan(setup):
    dispatcher, execution, plan, _ = setup
    old_plan = asdict(plan)
    for step in old_plan["steps"]:
        del step["task_approval_json"]
    old_payload = json.dumps(old_plan, sort_keys=True, separators=(",", ":"), allow_nan=False)
    dispatcher.journal.claim(
        execution, dispatcher.route, hashlib.sha256(old_payload.encode()).hexdigest()
    )
    dispatcher.journal.acknowledge(execution, "prior-run")
    await dispatcher.start(execution, plan)
    dispatcher.client.start_workflow.assert_not_awaited()
    dispatcher.client.get_workflow_handle.assert_not_called()
    changed = replace(
        plan,
        steps=(
            *plan.steps[:-1],
            Step(
                "approval", "approval", task_approval_json='{"rule":"any_one","user_ids":["alice"]}'
            ),
            plan.steps[-1],
        ),
    )
    with pytest.raises(StartError, match="another plan"):
        await dispatcher.start(execution, changed)
