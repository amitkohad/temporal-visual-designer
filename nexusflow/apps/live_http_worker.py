"""Explicit host-owned Temporal live/dual HTTP JSON composition for local-demo."""

import math
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from nexusflow.apps.local_live_http import LocalLiveHTTPWorkerAuthorization
from nexusflow.apps.local_secrets import LocalSecretStore
from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.authorized_execution import AuthorizedHTTPAttemptExecutor
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.resolver import AsyncIPv4Resolver
from nexusflow.modules.connectors.temporal_http import HTTPJSONActivities
from nexusflow.modules.connectors.transport import PinnedHTTPTransport

_CONFIGURATION_ERROR = "Invalid live HTTP worker configuration"


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


@contextmanager
def open_live_http_activities(
    directory: Path,
    scope: Scope,
    namespace: str,
    task_queue: str,
    *,
    secrets_file: str = "http-secrets.json",
    mock_fixtures_database: str | Path | None = None,
    timeout_seconds: float = 30,
) -> Iterator[HTTPJSONActivities]:
    """Yield dual-capable dispatch over connections, secrets, and optional fixtures.

    The host registers ``dispatch`` on its own Temporal worker. Stop that worker
    before leaving this context. Mock-only factories remain unchanged.
    """
    try:
        selected = _scope(scope)
        if (
            selected.environment_id != "dev"
            or type(namespace) is not str
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", namespace) is None
            or type(task_queue) is not str
            or not 1 <= len(task_queue) <= 255
            or task_queue.strip() != task_queue
            or any(ord(char) < 32 or ord(char) > 126 for char in task_queue)
            or type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
            or type(secrets_file) is not str
            or secrets_file != "http-secrets.json"
        ):
            raise ValueError
        duration = float(timeout_seconds)
        root = directory if type(directory) is Path else Path(directory)
    except Exception:
        raise ValueError(_CONFIGURATION_ERROR) from None

    with ExitStack() as stack:
        try:
            connections = SQLiteConnectionRepository(root / "connections.db")
            stack.callback(connections.close)
            secrets = LocalSecretStore(root / secrets_file, selected)
            authorization = LocalLiveHTTPWorkerAuthorization(selected)
            resolver = AsyncIPv4Resolver()
            planner = HTTPDestinationPlanner(connections, authorization, resolver, secrets=secrets)
            live = AuthorizedHTTPAttemptExecutor(
                planner, PinnedHTTPTransport(), timeout_seconds=min(30, duration)
            )
            mock = None
            if mock_fixtures_database is not None:
                scenarios = SQLiteHTTPMockScenarios(mock_fixtures_database, read_only=True)
                stack.callback(scenarios.close)
                policy = ExplicitDevHTTPMockPolicy(authorization, (selected,))
                mock = HTTPMockExecutor(policy, scenarios, timeout_seconds=min(5, duration))
            adapter = HTTPJSONActivityAdapter(live=live, mock=mock, timeout_seconds=duration)
            activities = HTTPJSONActivities(
                adapter, selected, namespace, task_queue, timeout_seconds=duration
            )
        except Exception:
            raise ValueError(_CONFIGURATION_ERROR) from None
        yield activities
