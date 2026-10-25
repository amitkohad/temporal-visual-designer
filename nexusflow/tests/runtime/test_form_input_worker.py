"""The worker must never persist unresolved authoring syntax as form data."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from nexusflow.tests.runtime.test_managed_form_config import FORM
from nexusflow.tests.runtime.test_task_creation import setup as setup


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["field", "nested", "array"])
async def test_unresolved_worker_binding_is_rejected_before_any_write(setup, placement):
    adapter, call, _, authorization = setup
    marker = {"$binding": {"schema_version": "0.1", "source": "input", "path": "/amount"}}
    value = (
        marker if placement == "field" else {"child": marker} if placement == "nested" else [marker]
    )
    configuration = {
        "rule": "any_one",
        "user_ids": ["alice"],
        "form": {"definition": FORM, "initial_values": {"amount": value}},
    }
    with pytest.raises(ValidationError, match="Unresolved form input bindings"):
        await adapter.create(replace(call, approval_json=json.dumps(configuration)))
    assert adapter.tasks._db.execute("SELECT COUNT(*) FROM human_tasks").fetchone()[0] == 0
    assert (
        adapter.journal._db.execute("SELECT COUNT(*) FROM temporal_task_completions").fetchone()[0]
        == 0
    )
    authorization.authorize.assert_not_called()
