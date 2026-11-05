"""Explicit read-only persistent fixture composition for a host-owned worker."""

import math
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.destination_types import WorkerDestinationAuthorization
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.temporal_mocks import HTTPJSONMockActivities

_CONFIGURATION_ERROR = "Invalid persistent HTTP mock worker configuration"


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


@contextmanager
def open_persistent_http_mock_activities(
    database: str | Path,
    worker_authorization: WorkerDestinationAuthorization,
    scope: Scope,
    namespace: str,
    task_queue: str,
    *,
    dev_scopes: tuple[Scope, ...],
    timeout_seconds: float = 30,
) -> Iterator[HTTPJSONMockActivities]:
    """Yield mock dispatch with fresh authorization and an existing read-only store.

    The host explicitly declares eligible Dev scopes and registers ``dispatch``
    on its own Temporal worker. Stop that worker before leaving this context.
    This factory creates no fixture, permission, worker or live execution port.
    SQLite may manage locking sidecars, but fixture data/schema remain read-only.
    """
    try:
        selected = _scope(scope)
        if (
            type(namespace) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", namespace) is None
            or type(task_queue) is not str
            or not 1 <= len(task_queue) <= 255
            or task_queue.strip() != task_queue
            or any(ord(char) < 32 or ord(char) > 126 for char in task_queue)
            or type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
            or type(dev_scopes) is not tuple
            or len(dev_scopes) > 1000
            or not callable(getattr(worker_authorization, "authorize", None))
        ):
            raise ValueError
        declared = tuple(_scope(item) for item in dev_scopes)
        policy = ExplicitDevHTTPMockPolicy(worker_authorization, declared)
        if selected not in declared:
            raise ValueError
        duration = float(timeout_seconds)
    except Exception:
        raise ValueError(_CONFIGURATION_ERROR) from None

    with ExitStack() as stack:
        scenarios = SQLiteHTTPMockScenarios(database, read_only=True)
        stack.callback(scenarios.close)
        try:
            executor = HTTPMockExecutor(policy, scenarios, timeout_seconds=min(5, duration))
            adapter = HTTPJSONActivityAdapter(mock=executor, timeout_seconds=duration)
            activities = HTTPJSONMockActivities(
                adapter, selected, namespace, task_queue, timeout_seconds=duration
            )
        except Exception:
            raise ValueError(_CONFIGURATION_ERROR) from None
        yield activities
