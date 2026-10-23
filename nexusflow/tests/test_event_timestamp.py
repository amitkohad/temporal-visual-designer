"""Reject plausible-looking timestamps that do not identify a real UTC instant."""

import json

import pytest
from test_contracts import load_fixture

from nexusflow.contracts import validate_contract


@pytest.mark.parametrize(
    "timestamp",
    ["2026-02-30T12:00:00Z", "2026-13-01T12:00:00Z", "2026-01-01T25:00:00Z"],
)
def test_calendar_invalid_timestamp_is_rejected(timestamp):
    payload = load_fixture("step-event")
    payload["timestamp"] = timestamp
    result = validate_contract("step-event", json.dumps(payload))
    assert result.value is None
    assert [(issue.path, issue.code) for issue in result.issues] == [("/timestamp", "value_error")]
