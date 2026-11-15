"""Explicit host-owned Temporal live email composition for local-demo."""

import math
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from nexusflow.apps.local_email_secrets import LocalEmailSecretStore
from nexusflow.apps.local_live_email import LocalLiveEmailWorkerAuthorization
from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.authorized_email_execution import AuthorizedEmailSendExecutor
from nexusflow.modules.connectors.email_activity import EmailTextActivityAdapter
from nexusflow.modules.connectors.email_connection_repository import SQLiteEmailConnectionRepository
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner
from nexusflow.modules.connectors.smtp_transport import PinnedSMTPTransport
from nexusflow.modules.connectors.temporal_email import EmailTextActivities

_CONFIGURATION_ERROR = "Invalid live email worker configuration"


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


@contextmanager
def open_live_email_activities(
    directory: Path,
    scope: Scope,
    namespace: str,
    task_queue: str,
    *,
    secrets_file: str = "email-secrets.json",
    timeout_seconds: float = 30,
) -> Iterator[EmailTextActivities]:
    """Yield live email dispatch over email connections, secrets, and SMTPS.

    The host registers ``dispatch`` (or a combined proof router) on its own
    Temporal worker. Stop that worker before leaving this context.
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
            or secrets_file != "email-secrets.json"
        ):
            raise ValueError
        duration = float(timeout_seconds)
        root = directory if type(directory) is Path else Path(directory)
    except Exception:
        raise ValueError(_CONFIGURATION_ERROR) from None

    with ExitStack() as stack:
        try:
            connections = SQLiteEmailConnectionRepository(root / "email-connections.db")
            stack.callback(connections.close)
            secrets = LocalEmailSecretStore(root / secrets_file, selected)
            authorization = LocalLiveEmailWorkerAuthorization(selected)
            planner = EmailPreflightPlanner(
                connections, authorization, timeout_seconds=min(5, duration)
            )
            live = AuthorizedEmailSendExecutor(
                planner, secrets, PinnedSMTPTransport(), timeout_seconds=min(30, duration)
            )
            adapter = EmailTextActivityAdapter(live=live, timeout_seconds=duration)
            activities = EmailTextActivities(
                adapter, selected, namespace, task_queue, timeout_seconds=duration
            )
        except Exception:
            raise ValueError(_CONFIGURATION_ERROR) from None
        yield activities
