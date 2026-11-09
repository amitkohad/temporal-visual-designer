"""Fresh worker policy and bounded private reads; no email delivery effects."""

import asyncio
import inspect
import math

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.destination_types import WorkerDestinationAuthorization
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionRecord,
    EmailConnectionRepository,
)
from nexusflow.modules.connectors.email_envelope import prepare_email_envelope
from nexusflow.modules.connectors.email_preflight_types import (
    EmailPreflightError,
    PreparedEmailEnvelope,
)
from nexusflow.modules.connectors.email_types import (
    EmailTextActivityInput,
    prepare_email_text_message,
)
from nexusflow.modules.connectors.types import ConnectionError


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


def _identifier(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Identifier).validate_python(value, strict=True)


class EmailPreflightPlanner:
    """Prepare private configuration only, with fresh resource-bound permission.

    An envelope is not an egress or sending permit. A cancelled storage await
    does not forcibly interrupt its already running read-only repository thread.
    """

    def __init__(
        self,
        repository: EmailConnectionRepository,
        authorization: WorkerDestinationAuthorization,
        *,
        timeout_seconds: float = 5,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("Invalid email preflight timeout") from None
        self.repository = repository
        self.authorization = authorization
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _checkpoint(deadline: asyncio.Timeout, cancellation_count: int) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > cancellation_count:
            raise asyncio.CancelledError
        expires = deadline.when()
        if deadline.expired() or (
            expires is not None and asyncio.get_running_loop().time() >= expires
        ):
            raise TimeoutError

    def _authorize(self, scope: Scope, connection_id: str) -> None:
        try:
            actor = self.authorization.authorize(_scope(scope), "worker.execute", connection_id)
        except Exception as error:
            try:
                code = getattr(error, "code", None)
                forbidden = isinstance(error, PermissionError) or (
                    type(code) is str and code in {"forbidden", "unauthenticated"}
                )
            except Exception:
                forbidden = False
            raise EmailPreflightError("forbidden" if forbidden else "unavailable") from None
        try:
            if inspect.iscoroutine(actor):
                actor.close()
                raise ValueError
            _identifier(actor)
        except Exception:
            raise EmailPreflightError("invalid_provider_result") from None

    async def _read(self, scope: Scope, inputs: EmailTextActivityInput) -> object:
        try:
            return await asyncio.to_thread(
                self.repository.get,
                _scope(scope),
                inputs.connection_id,
                inputs.connection_revision,
            )
        except ConnectionError as error:
            try:
                code = error.code
                missing = type(code) is str and code == "not_found"
            except Exception:
                missing = False
            raise EmailPreflightError("not_found" if missing else "unavailable") from None
        except Exception:
            raise EmailPreflightError("unavailable") from None

    async def prepare(
        self, scope: Scope, operation_id: str, inputs: EmailTextActivityInput
    ) -> PreparedEmailEnvelope:
        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                try:
                    selected = _scope(scope)
                    operation = _identifier(operation_id)
                    message = prepare_email_text_message(inputs)
                    snapshot = EmailTextActivityInput(
                        connection_id=message.connection_id,
                        connection_revision=message.connection_revision,
                        execution_mode=message.execution_mode,
                        fixture_digest=message.fixture_digest,
                        to=list(message.to),
                        cc=list(message.cc),
                        bcc=list(message.bcc),
                        subject=message.subject,
                        text_body=message.text_body,
                    )
                except Exception:
                    raise EmailPreflightError("invalid_request") from None
                if snapshot.execution_mode != "live":
                    raise EmailPreflightError("forbidden") from None
                self._checkpoint(deadline, cancellation_count)
                self._authorize(selected, snapshot.connection_id)
                self._checkpoint(deadline, cancellation_count)
                record = await self._read(selected, snapshot)
                self._checkpoint(deadline, cancellation_count)
                self._authorize(selected, snapshot.connection_id)
                self._checkpoint(deadline, cancellation_count)
                if inspect.iscoroutine(record):
                    record.close()
                    raise EmailPreflightError("invalid_provider_result") from None
                if type(record) is not EmailConnectionRecord:
                    raise EmailPreflightError("invalid_provider_result") from None
                envelope = prepare_email_envelope(selected, operation, record, snapshot)
                self._checkpoint(deadline, cancellation_count)
                return envelope
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise EmailPreflightError("timeout") from None
        except EmailPreflightError as error:
            try:
                code = error.code
            except Exception:
                code = "unavailable"
            raise EmailPreflightError(code) from None
        except Exception:
            raise EmailPreflightError("unavailable") from None
