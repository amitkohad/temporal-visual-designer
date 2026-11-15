"""Authorized email send resolves secrets before network and redacts credentials."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.authorized_email_execution import (
    AuthorizedEmailSendExecutor,
    EmailSendError,
)
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope
from nexusflow.modules.connectors.email_types import EmailTextActivityInput
from nexusflow.modules.connectors.secret_types import SecretResolverError
from nexusflow.modules.connectors.smtp_secret_types import SMTPLoginCredential
from nexusflow.modules.connectors.smtp_transport_types import SMTPSendRequest, SMTPSendResult

SCOPE = Scope(organization_id="organization", environment_id="dev")
REFERENCE = "hr-mail-credentials-v1"
PASSWORD = "never-leak-smtp-password"
NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def envelope(**updates):
    base = dict(
        scope=SCOPE,
        operation_id="op-1",
        connection_id="hr-mail",
        connection_revision="v1",
        definition_digest="sha256:" + "a" * 64,
        execution_mode="live",
        smtp_host="smtp.private.test",
        port=465,
        sender="hr@example.test",
        to=("employee@example.test",),
        cc=(),
        bcc=(),
        subject="Welcome",
        text_body="Hello",
        secret_reference=REFERENCE,
    )
    base.update(updates)
    return PreparedEmailEnvelope(**base)


class Planner:
    def __init__(self, value=None, *, missing_secret=False):
        self.value = value or envelope(secret_reference=None if missing_secret else REFERENCE)
        self.authorization = Mock()
        self.authorization.authorize.return_value = "worker-1"
        self.calls = 0
        self.network = 0

    async def prepare(self, scope, operation_id, inputs):
        self.calls += 1
        assert scope == SCOPE and operation_id == "op-1"
        assert inputs.execution_mode == "live"
        return self.value


class Secrets:
    def __init__(self, *, missing=False):
        self.missing = missing
        self.calls = 0

    def resolve(self, scope, secret_reference):
        self.calls += 1
        assert scope == SCOPE and secret_reference == REFERENCE
        if self.missing:
            raise SecretResolverError("credentials_required")
        return SMTPLoginCredential("login", "smtp-user", PASSWORD)


class Sender:
    def __init__(self):
        self.calls = []

    def send(self, operation_id, request, *, before_write=None, timeout_seconds=30):
        assert isinstance(request, SMTPSendRequest)
        assert request.credential is not None
        assert request.credential.password == PASSWORD
        if before_write is not None:
            before_write()
        self.calls.append(operation_id)
        return SMTPSendResult(operation_id, "accepted")


def inputs():
    return EmailTextActivityInput(
        connection_id="hr-mail",
        connection_revision="v1",
        execution_mode="live",
        to=["employee@example.test"],
        subject="Welcome",
        text_body="Hello",
    )


@pytest.mark.asyncio
async def test_send_resolves_secret_before_sender_and_hides_password():
    planner = Planner()
    secrets = Secrets()
    sender = Sender()
    result = await AuthorizedEmailSendExecutor(planner, secrets, sender).execute(
        SCOPE, "op-1", inputs(), message_time=NOW
    )
    assert result.submission_status == "accepted"
    assert result.execution_mode == "live"
    assert secrets.calls >= 2
    assert sender.calls == ["op-1"]
    assert PASSWORD not in repr(result)
    assert REFERENCE not in repr(result)
    assert PASSWORD not in repr(sender.calls)


@pytest.mark.asyncio
async def test_missing_secret_fails_without_sender():
    planner = Planner()
    secrets = Secrets(missing=True)
    sender = Sender()
    with pytest.raises(EmailSendError) as error:
        await AuthorizedEmailSendExecutor(planner, secrets, sender).execute(
            SCOPE, "op-1", inputs(), message_time=NOW
        )
    assert error.value.code == "credentials_required"
    assert sender.calls == []
    assert secrets.calls == 1


@pytest.mark.asyncio
async def test_absent_secret_reference_requires_credentials_without_sender():
    planner = Planner(missing_secret=True)
    secrets = Secrets()
    sender = Sender()
    with pytest.raises(EmailSendError) as error:
        await AuthorizedEmailSendExecutor(planner, secrets, sender).execute(
            SCOPE, "op-1", inputs(), message_time=NOW
        )
    assert error.value.code == "credentials_required"
    assert secrets.calls == 0
    assert sender.calls == []
