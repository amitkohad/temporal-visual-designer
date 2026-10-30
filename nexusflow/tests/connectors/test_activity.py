"""HTTP JSON activity mapping and private failure semantics against isolated ports."""

import asyncio
import math
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors import activity as activity_module
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_types import HTTPActivityError, HTTPJSONActivityInput
from nexusflow.modules.connectors.execution_types import HTTPExecutionError
from nexusflow.modules.connectors.mock_types import HTTPMockError
from nexusflow.modules.connectors.transport_types import HTTPAttemptResult

SCOPE = Scope(organization_id="acme", environment_id="dev")
PIN = "sha256:" + "a" * 64
PRIVATE = "never-expose-provider-secret"
RESULT = HTTPAttemptResult("step-1", 200, (("set-cookie", PRIVATE),), PRIVATE.encode())


def inputs(**changes):
    return {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "live",
        "method": "POST",
        "path": "/employees?private=" + PRIVATE,
        "json_body": {"z": [1, True, None], "a": "héllo"},
        **changes,
    }


@pytest.fixture
def ports():
    live, mock = AsyncMock(), AsyncMock()
    live.execute.return_value = mock.execute.return_value = RESULT
    return live, mock


async def failed(adapter, raw, code, *, possible=False):
    with pytest.raises(HTTPActivityError) as caught:
        await adapter.execute(SCOPE, "step-1", raw)
    error = caught.value
    assert error.code == code and error.delivery_may_have_occurred is possible
    assert error.retryable is False
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


@pytest.mark.asyncio
async def test_live_mapping_is_canonical_utf8_and_output_drops_all_provider_values(ports):
    live, mock = ports
    result = await HTTPJSONActivityAdapter(live=live, mock=mock).execute(SCOPE, "step-1", inputs())
    assert result.model_dump() == {
        "operation_id": "step-1",
        "status_code": 200,
        "execution_mode": "live",
    }
    assert PRIVATE not in repr(result) + result.model_dump_json()
    scope, destination, request = live.execute.call_args.args
    assert scope == SCOPE and destination.operation_id == "step-1"
    assert destination.connection_id == "hr" and destination.connection_revision == "v1"
    assert destination.path == "/employees?private=" + PRIVATE
    assert request.body == '{"a":"héllo","z":[1,true,null]}'.encode()
    assert request.content_type == "application/json" and request.method == "POST"
    live.execute.assert_awaited_once()
    mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_mock_mode_is_explicit_pinned_and_identified_in_status_output(ports):
    live, mock = ports
    result = await HTTPJSONActivityAdapter(live=live, mock=mock).execute(
        SCOPE, "step-1", inputs(execution_mode="mock", fixture_digest=PIN)
    )
    assert result.execution_mode == "mock" and result.status_code == 200
    assert mock.execute.call_args.kwargs == {"fixture_digest": PIN}
    live.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_omitted_json_body_maps_to_no_bytes(method, ports):
    live, _ = ports
    live.execute.return_value = replace(RESULT, body=b"")
    await HTTPJSONActivityAdapter(live=live).execute(
        SCOPE, "step-1", inputs(method=method, json_body=None)
    )
    assert live.execute.call_args.args[2].body == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "mock"])
async def test_missing_selected_port_never_falls_back(mode, ports):
    live, mock = ports
    adapter = HTTPJSONActivityAdapter(
        live=None if mode == "live" else live, mock=None if mode == "mock" else mock
    )
    await failed(
        adapter,
        inputs(execution_mode=mode, fixture_digest=PIN if mode == "mock" else None),
        "unavailable",
    )
    live.execute.assert_not_called()
    mock.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"scope": {"environment_id": "prod"}},
        {"operation_id": "attacker"},
        {"headers": {"authorization": PRIVATE}},
        {"secret_reference": PRIVATE},
        {"connection_id": "bad id"},
        {"connection_revision": ""},
        {"execution_mode": "auto"},
        {"fixture_digest": PIN},
        {"execution_mode": "mock"},
        {"execution_mode": "mock", "fixture_digest": "invalid"},
        {"method": "get"},
        {"method": "CONNECT"},
        {"method": "GET"},
        {"method": "HEAD"},
        {"path": "https://other.invalid"},
        {"path": "//other.invalid"},
        {"path": "/bad\npath"},
        {"path": "/bad%0apath"},
        {"path": ""},
        {"path": "/" + "x" * 4096},
        {"json_body": []},
        {"json_body": PRIVATE},
        {"json_body": {"a": b"bad"}},
        {"json_body": {1: "bad"}},
        {"json_body": {"a": math.nan}},
        {"json_body": {"a": math.inf}},
        {"json_body": {"a": PRIVATE * 50000}},
    ],
)
async def test_invalid_business_input_fails_before_ports(change, ports):
    live, mock = ports
    await failed(HTTPJSONActivityAdapter(live=live, mock=mock), inputs(**change), "invalid_request")
    live.execute.assert_not_called()
    mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_cyclic_deep_and_wide_json_are_rejected_before_execution(ports):
    live, mock = ports
    cyclic = {}
    cyclic["cycle"] = cyclic
    deep = {}
    cursor = deep
    for _ in range(34):
        cursor["child"] = {}
        cursor = cursor["child"]
    for body in (cyclic, deep, {"items": list(range(10001))}):
        await failed(
            HTTPJSONActivityAdapter(live=live, mock=mock), inputs(json_body=body), "invalid_request"
        )
    live.execute.assert_not_called()


@pytest.mark.asyncio
async def test_constructed_input_is_revalidated_without_serializer_warning(ports, recwarn):
    live, _ = ports
    raw = HTTPJSONActivityInput.model_construct(**inputs(json_body={"bad": object()}))
    await failed(HTTPJSONActivityAdapter(live=live), raw, "invalid_request")
    assert not recwarn


@pytest.mark.asyncio
async def test_mutable_input_model_is_snapshotted_before_provider_await(ports):
    live, _ = ports
    raw = HTTPJSONActivityInput.model_validate(inputs())

    async def execute(scope, destination, request):
        raw.json_body["a"] = "changed"
        object.__setattr__(raw, "path", "/changed")
        assert request.body == '{"a":"héllo","z":[1,true,null]}'.encode()
        assert destination.path == "/employees?private=" + PRIVATE
        return RESULT

    live.execute.side_effect = execute
    await HTTPJSONActivityAdapter(live=live).execute(SCOPE, "step-1", raw)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,operation", [(None, "step-1"), (SCOPE, ""), (SCOPE, False), (SCOPE, "bad id")]
)
async def test_trusted_context_is_strict(scope, operation, ports):
    live, _ = ports
    with pytest.raises(HTTPActivityError) as caught:
        await HTTPJSONActivityAdapter(live=live).execute(scope, operation, inputs())
    assert caught.value.code == "invalid_request" and not caught.value.delivery_may_have_occurred
    live.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 301, 307, 400, 401, 429, 500, 599])
async def test_valid_http_statuses_are_results_not_retries(status, ports):
    live, _ = ports
    live.execute.return_value = replace(RESULT, status_code=status)
    result = await HTTPJSONActivityAdapter(live=live).execute(SCOPE, "step-1", inputs())
    assert result.status_code == status and live.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"status_code": 200},
        replace(RESULT, operation_id="wrong"),
        replace(RESULT, status_code=True),
        replace(RESULT, status_code=199),
        replace(RESULT, status_code=600),
        replace(RESULT, body="not-bytes"),
        replace(RESULT, body=b"x" * 1048577),
        replace(RESULT, headers=[]),
        replace(RESULT, headers=(("Header", "value"),)),
        replace(RESULT, headers=(("x", "bad\nvalue"),)),
        replace(RESULT, headers=(("x", "💥"),)),
        replace(RESULT, headers=(("x", "y" * 16384),)),
        replace(RESULT, headers=(("x", "y"),) * 101),
        replace(RESULT, headers=(("x",),)),
        replace(RESULT, status_code=204),
        replace(RESULT, status_code=304),
    ],
)
async def test_malformed_results_never_expose_raw_values(result, ports):
    live, mock = ports
    live.execute.return_value = mock.execute.return_value = result
    adapter = HTTPJSONActivityAdapter(live=live, mock=mock)
    await failed(adapter, inputs(), "invalid_provider_result", possible=True)
    await failed(
        adapter, inputs(execution_mode="mock", fixture_digest=PIN), "invalid_provider_result"
    )


@pytest.mark.asyncio
async def test_oversized_provider_header_is_rejected_before_regex_scan(ports, monkeypatch):
    live, _ = ports
    live.execute.return_value = replace(RESULT, headers=(("x", PRIVATE * 100000),))
    calls = []

    def forbidden_scan(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Oversized header must fail before regex scanning")

    monkeypatch.setattr(activity_module, "re", SimpleNamespace(fullmatch=forbidden_scan))
    await failed(
        HTTPJSONActivityAdapter(live=live), inputs(), "invalid_provider_result", possible=True
    )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("possible", [False, True])
async def test_known_live_error_preserves_trusted_delivery_flag(possible, ports):
    live, _ = ports
    live.execute.side_effect = HTTPExecutionError("forbidden", delivery_may_have_occurred=possible)
    await failed(HTTPJSONActivityAdapter(live=live), inputs(), "forbidden", possible=possible)


@pytest.mark.asyncio
async def test_malformed_known_flag_stays_conservative(ports):
    live, _ = ports
    error = HTTPExecutionError("unavailable")
    error.delivery_may_have_occurred = PRIVATE
    live.execute.side_effect = error
    await failed(HTTPJSONActivityAdapter(live=live), inputs(), "unavailable", possible=True)


@pytest.mark.asyncio
async def test_wrong_error_family_never_downgrades_live_delivery(ports):
    live, mock = ports
    live.execute.side_effect = HTTPMockError("forbidden")
    mock.execute.side_effect = HTTPExecutionError("forbidden", delivery_may_have_occurred=True)
    adapter = HTTPJSONActivityAdapter(live=live, mock=mock)
    await failed(adapter, inputs(), "unavailable", possible=True)
    await failed(adapter, inputs(execution_mode="mock", fixture_digest=PIN), "unavailable")


@pytest.mark.asyncio
async def test_mock_errors_and_unknown_provider_text_are_private(ports):
    live, mock = ports
    mock.execute.side_effect = HTTPMockError("fixture_mismatch")
    adapter = HTTPJSONActivityAdapter(live=live, mock=mock)
    await failed(adapter, inputs(execution_mode="mock", fixture_digest=PIN), "fixture_mismatch")
    live.execute.side_effect = RuntimeError(PRIVATE)
    await failed(adapter, inputs(), "unavailable", possible=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "mock"])
async def test_timeout_is_bounded_and_delivery_is_mode_aware(mode, ports):
    live, mock = ports

    async def stall(*args, **kwargs):
        await asyncio.Event().wait()

    live.execute.side_effect = mock.execute.side_effect = stall
    await failed(
        HTTPJSONActivityAdapter(live=live, mock=mock, timeout_seconds=0.01),
        inputs(execution_mode=mode, fixture_digest=PIN if mode == "mock" else None),
        "timeout",
        possible=mode == "live",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "mock"])
async def test_suppressed_caller_cancellation_still_propagates(mode, ports):
    live, mock = ports
    entered = asyncio.Event()

    async def suppress(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return RESULT

    live.execute.side_effect = mock.execute.side_effect = suppress
    task = asyncio.create_task(
        HTTPJSONActivityAdapter(live=live, mock=mock).execute(
            SCOPE,
            "step-1",
            inputs(execution_mode=mode, fixture_digest=PIN if mode == "mock" else None),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_late_result_after_suppressed_timeout_is_rejected(ports):
    live, _ = ports

    async def suppress(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return RESULT

    live.execute.side_effect = suppress
    await failed(
        HTTPJSONActivityAdapter(live=live, timeout_seconds=0.01), inputs(), "timeout", possible=True
    )


@pytest.mark.asyncio
async def test_blocking_provider_cannot_return_success_after_deadline(ports):
    live, _ = ports

    async def delayed(*args):
        time.sleep(0.02)
        return RESULT

    live.execute.side_effect = delayed
    await failed(
        HTTPJSONActivityAdapter(live=live, timeout_seconds=0.005),
        inputs(),
        "timeout",
        possible=True,
    )


@pytest.mark.parametrize("value", [False, 0, -1, 31, math.inf, math.nan, "30"])
def test_invalid_timeout_configuration(value):
    with pytest.raises(ValueError, match="Invalid HTTP JSON activity timeout"):
        HTTPJSONActivityAdapter(timeout_seconds=value)
