"""Fresh worker policy and real deadline semantics without email delivery."""

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors import email_preflight as module
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionRecord,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner
from nexusflow.modules.connectors.email_preflight_types import EmailPreflightError
from nexusflow.modules.connectors.email_types import EmailTextActivityInput
from nexusflow.modules.connectors.types import ConnectionError
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import canonical, digest

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "private-email-preflight-value"


def inputs(**changes):
    value = {
        "connection_id": "mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["Employee@EXAMPLE.TEST"],
        "cc": ["manager@example.test"],
        "bcc": ["audit@example.test"],
        "subject": PRIVATE,
        "text_body": PRIVATE + "\nline two",
    }
    value.update(changes)
    return EmailTextActivityInput.model_validate(value, strict=True)


def record():
    definition = EmailConnectionWrite(
        smtp_host="smtp.example.test",
        sender="trusted@example.test",
        allowed_recipient_domains=("example.test",),
        secret_reference=PRIVATE,
    ).model_dump(mode="json")
    metadata = EmailConnectionMetadata(
        scope=SCOPE.model_copy(deep=True),
        connection_id="mail",
        connection_revision="v1",
        revision=1,
        definition_digest=digest(definition),
        has_secret_reference=True,
        actor_id="administrator",
        created_at=datetime(2026, 11, 9, tzinfo=UTC).isoformat(),
    )
    return EmailConnectionRecord(metadata, canonical(definition))


class Authorization:
    def __init__(self, events):
        self.events = events
        self.calls = []
        self.denied = False
        self.failure = None
        self.actor = "worker"

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        self.events.append("authorize")
        if self.failure is not None:
            raise self.failure
        if self.denied:
            raise PermissionError(PRIVATE)
        return self.actor


@pytest.fixture
def ports():
    events = []
    authorization = Authorization(events)
    repository = Mock()

    def read(scope, connection_id, revision):
        events.append("get")
        assert scope == SCOPE and connection_id == "mail" and revision == "v1"
        return record()

    repository.get.side_effect = read
    return repository, authorization, events


async def failure(planner, code, *, scope=None, operation_id="operation", message=None):
    with pytest.raises(EmailPreflightError) as caught:
        await planner.prepare(
            SCOPE if scope is None else scope,
            operation_id,
            inputs() if message is None else message,
        )
    assert caught.value.code == code
    assert str(caught.value) == "Email preflight failed"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.retryable is False
    assert caught.value.delivery_may_have_occurred is False
    assert caught.value.__suppress_context__
    return caught.value


@pytest.mark.asyncio
async def test_scope_bound_fresh_authorization_and_private_prepared_result(ports):
    repository, authorization, events = ports
    prepared = await EmailPreflightPlanner(repository, authorization).prepare(
        SCOPE, "operation", inputs()
    )
    assert events == ["authorize", "get", "authorize"]
    assert all(call == (SCOPE, "worker.execute", "mail") for call in authorization.calls)
    assert prepared.sender == "trusted@example.test"
    assert prepared.smtp_host == "smtp.example.test" and prepared.port == 465
    assert prepared.to == ("Employee@example.test",)
    assert prepared.secret_reference == PRIVATE
    assert prepared.operation_id == "operation" and prepared.scope == SCOPE
    assert PRIVATE not in repr(prepared) and "Employee@" not in repr(prepared)
    assert prepared.scope is not SCOPE


@pytest.mark.parametrize("timeout", [0, -1, 31, True, False, "5", None, float("nan"), float("inf")])
def test_invalid_deadline_configuration_is_private(ports, timeout):
    repository, authorization, _ = ports
    with pytest.raises(ValueError, match="^Invalid email preflight timeout$"):
        EmailPreflightPlanner(repository, authorization, timeout_seconds=timeout)
    repository.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["", "wrong:operation", 1, True, PRIVATE + "/"])
async def test_invalid_host_operation_rejected_before_policy_or_storage(ports, operation):
    repository, authorization, events = ports
    await failure(
        EmailPreflightPlanner(repository, authorization), "invalid_request", operation_id=operation
    )
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    ["acme", {}, Scope.model_construct(organization_id=PRIVATE + "/", environment_id="dev")],
)
async def test_invalid_scope_is_not_authorized(ports, scope):
    repository, authorization, events = ports
    await failure(EmailPreflightPlanner(repository, authorization), "invalid_request", scope=scope)
    assert events == []


@pytest.mark.asyncio
async def test_tampered_message_is_revalidated_before_any_private_read(ports):
    repository, authorization, events = ports
    message = inputs()
    message.to.append(PRIVATE + "\r\n@example.test")
    await failure(
        EmailPreflightPlanner(repository, authorization), "invalid_request", message=message
    )
    assert events == []


@pytest.mark.asyncio
async def test_mock_mode_denied_without_policy_or_private_storage(ports):
    repository, authorization, events = ports
    message = inputs(execution_mode="mock", fixture_digest="sha256:" + "a" * 64)
    await failure(EmailPreflightPlanner(repository, authorization), "forbidden", message=message)
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (PermissionError(PRIVATE), "forbidden"),
        (IdentityError("forbidden", PRIVATE), "forbidden"),
        (IdentityError("unauthenticated", PRIVATE), "forbidden"),
        (IdentityError("unavailable", PRIVATE), "unavailable"),
        (RuntimeError(PRIVATE), "unavailable"),
    ],
)
async def test_authorization_errors_precede_private_lookup(ports, error, code):
    repository, authorization, events = ports
    authorization.failure = error
    await failure(EmailPreflightPlanner(repository, authorization), code)
    assert events == ["authorize"]
    repository.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, True, "bad/actor", 1, {}])
async def test_invalid_authorization_result_cannot_reach_storage(ports, actor):
    repository, authorization, _ = ports
    authorization.actor = actor
    await failure(EmailPreflightPlanner(repository, authorization), "invalid_provider_result")
    repository.get.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_during_read_blocks_result(ports):
    repository, authorization, events = ports

    def revoke(*args):
        events.append("get")
        authorization.denied = True
        return record()

    repository.get.side_effect = revoke
    await failure(EmailPreflightPlanner(repository, authorization), "forbidden")
    assert events == ["authorize", "get", "authorize"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (ConnectionError("not_found", PRIVATE), "not_found"),
        (ConnectionError("forbidden", PRIVATE), "unavailable"),
        (ConnectionError("invalid_request", PRIVATE), "unavailable"),
        (ConnectionError("unavailable", PRIVATE), "unavailable"),
        (RuntimeError(PRIVATE), "unavailable"),
        (TimeoutError(PRIVATE), "unavailable"),
    ],
)
async def test_storage_failures_have_closed_private_mapping(ports, error, code):
    repository, authorization, events = ports
    repository.get.side_effect = error
    await failure(EmailPreflightPlanner(repository, authorization), code)
    assert events == ["authorize"]


class HostileCode:
    def __eq__(self, other):
        raise AssertionError(PRIVATE)

    def __hash__(self):
        raise AssertionError(PRIVATE)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["authorization", "storage"])
async def test_hostile_provider_code_is_not_compared_or_exposed(ports, source):
    repository, authorization, _ = ports
    error = ConnectionError("unavailable", PRIVATE)
    error.code = HostileCode()
    if source == "storage":
        repository.get.side_effect = error
    else:
        authorization.failure = error
    await failure(EmailPreflightPlanner(repository, authorization), "unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, {}, "private-record", True])
async def test_malformed_private_record_cannot_be_released(ports, value):
    repository, authorization, events = ports
    repository.get.side_effect = None
    repository.get.return_value = value
    await failure(EmailPreflightPlanner(repository, authorization), "invalid_provider_result")
    assert events == ["authorize", "authorize"]


@pytest.mark.asyncio
async def test_foreign_record_is_rejected_after_fresh_policy(ports):
    repository, authorization, _ = ports
    value = record()
    foreign = value.metadata.model_copy(
        update={"scope": Scope(organization_id="foreign", environment_id="dev")}
    )
    repository.get.side_effect = None
    repository.get.return_value = replace(value, metadata=foreign)
    await failure(EmailPreflightPlanner(repository, authorization), "invalid_provider_result")


@pytest.mark.asyncio
async def test_domain_denial_is_enforced_after_private_lookup(ports):
    repository, authorization, _ = ports
    await failure(
        EmailPreflightPlanner(repository, authorization),
        "blocked_recipient",
        message=inputs(bcc=["audit@sub.example.test"]),
    )
    repository.get.assert_called_once()


@pytest.mark.asyncio
async def test_sqlite_read_runs_off_event_loop_and_inputs_are_frozen(ports):
    repository, authorization, _ = ports
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    threads = []

    def blocked(scope, connection_id, revision):
        threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(10)
            assert scope == SCOPE and connection_id == "mail" and revision == "v1"
            return record()
        finally:
            finished.set()

    repository.get.side_effect = blocked
    message = inputs()
    scope = SCOPE.model_copy(deep=True)
    task = asyncio.create_task(
        EmailPreflightPlanner(repository, authorization).prepare(scope, "operation", message)
    )
    try:
        await entered.wait()
        message.to.append("attacker@blocked.test")
        object.__setattr__(message, "subject", "changed")
        object.__setattr__(scope, "organization_id", "changed")
        release.set()
        result = await task
        assert result.to == ("Employee@example.test",)
        assert result.subject == PRIVATE and result.scope == SCOPE
        assert threads and threads[0] != main_thread and finished.is_set()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress", [False, True])
async def test_real_deadline_rejects_stalled_or_suppressed_read(ports, monkeypatch, suppress):
    repository, authorization, _ = ports
    deadlines, stopped = [], asyncio.Event()
    original_timeout = asyncio.timeout

    def capture(delay):
        deadline = original_timeout(delay)
        deadlines.append(deadline)
        return deadline

    async def blocked(*args, **kwargs):
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if suppress:
                return record()
            raise
        finally:
            stopped.set()

    monkeypatch.setattr(module.asyncio, "timeout", capture)
    monkeypatch.setattr(module.asyncio, "to_thread", blocked)
    await failure(EmailPreflightPlanner(repository, authorization, timeout_seconds=30), "timeout")
    assert stopped.is_set() and len(deadlines) == 1 and deadlines[0].expired()


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress", [False, True])
async def test_delivered_caller_cancellation_never_releases_result(ports, monkeypatch, suppress):
    repository, authorization, _ = ports
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if suppress:
                return record()
            raise
        finally:
            stopped.set()

    monkeypatch.setattr(module.asyncio, "to_thread", blocked)
    task = asyncio.create_task(
        EmailPreflightPlanner(repository, authorization).prepare(SCOPE, "operation", inputs())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_read", "after_read", "envelope"])
async def test_late_synchronous_answers_cannot_escape_deadline(ports, monkeypatch, phase):
    repository, authorization, _ = ports
    original_timeout = asyncio.timeout
    deadlines = []

    def capture(delay):
        deadline = original_timeout(delay)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(module.asyncio, "timeout", capture)
    original_authorize = authorization.authorize

    def authorize(*args):
        value = original_authorize(*args)
        target = 1 if phase == "before_read" else 2
        if phase != "envelope" and len(authorization.calls) == target:
            deadlines[0].reschedule(asyncio.get_running_loop().time() - 1)
        return value

    authorization.authorize = authorize
    original_prepare = module.prepare_email_envelope

    def late_envelope(*args):
        value = original_prepare(*args)
        deadlines[0].reschedule(asyncio.get_running_loop().time() - 1)
        return value

    if phase == "envelope":
        monkeypatch.setattr(module, "prepare_email_envelope", late_envelope)
    await failure(EmailPreflightPlanner(repository, authorization, timeout_seconds=30), "timeout")
    if phase == "before_read":
        repository.get.assert_not_called()


@pytest.mark.asyncio
async def test_actual_thread_read_may_finish_after_cancel_without_releasing_result(ports):
    repository, authorization, _ = ports
    loop = asyncio.get_running_loop()
    entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()

    def blocked(*args):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(10)
            return record()
        finally:
            finished.set()

    repository.get.side_effect = blocked
    task = asyncio.create_task(
        EmailPreflightPlanner(repository, authorization).prepare(SCOPE, "operation", inputs())
    )
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        assert len(authorization.calls) == 1
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["authorization", "repository"])
async def test_async_provider_returned_in_sync_port_is_closed_privately(ports, source):
    repository, authorization, _ = ports

    async def unsupported():
        return record()

    result = unsupported()
    if source == "authorization":
        authorization.actor = result
    else:
        repository.get.side_effect = None
        repository.get.return_value = result
    await failure(EmailPreflightPlanner(repository, authorization), "invalid_provider_result")
    assert result.cr_frame is None


@pytest.mark.asyncio
async def test_unexpected_envelope_policy_failure_is_private(ports, monkeypatch):
    repository, authorization, _ = ports
    monkeypatch.setattr(module, "prepare_email_envelope", Mock(side_effect=RuntimeError(PRIVATE)))
    await failure(EmailPreflightPlanner(repository, authorization), "unavailable")


@pytest.mark.asyncio
async def test_authorizer_and_repository_cannot_mutate_owned_scope(ports):
    repository, authorization, _ = ports
    original_authorize = authorization.authorize

    def mutate_policy(scope, *args):
        actor = original_authorize(scope, *args)
        object.__setattr__(scope, "organization_id", "mutated")
        return actor

    def mutate_storage(scope, *args):
        object.__setattr__(scope, "organization_id", "mutated")
        return record()

    authorization.authorize = mutate_policy
    repository.get.side_effect = mutate_storage
    result = await EmailPreflightPlanner(repository, authorization).prepare(
        SCOPE, "operation", inputs()
    )
    assert result.scope == SCOPE and SCOPE.organization_id == "acme"
