"""Pinned SMTPS send; single attempt with private AUTH, no retry fallback."""

import math
import smtplib
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from nexusflow.modules.connectors.smtp_transport_types import (
    SMTPSendRequest,
    SMTPSendResult,
    SMTPTransportError,
)


class PinnedSMTPTransport:
    """Send one encoded message over SMTP_SSL with AUTH LOGIN credentials."""

    def __init__(self, *, smtp_factory: Callable[..., Any] | None = None) -> None:
        self.smtp_factory = smtp_factory or smtplib.SMTP_SSL

    def send(
        self,
        operation_id: str,
        request: SMTPSendRequest,
        *,
        before_write: Callable[[], None] | None = None,
        timeout_seconds: float = 30,
    ) -> SMTPSendResult:
        if (
            type(operation_id) is not str
            or not 1 <= len(operation_id) <= 255
            or type(request) is not SMTPSendRequest
            or type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 120
            or not math.isfinite(timeout_seconds)
        ):
            raise SMTPTransportError("invalid_request") from None
        if request.credential is None:
            raise SMTPTransportError("credentials_required") from None
        if (
            type(request.smtp_host) is not str
            or not request.smtp_host
            or type(request.port) is not int
            or not 1 <= request.port <= 65535
            or type(request.envelope_sender) is not str
            or type(request.recipients) is not tuple
            or not request.recipients
            or type(request.data) is not bytes
            or not request.data
        ):
            raise SMTPTransportError("invalid_request") from None

        client = None
        data_accepted = False
        try:
            if before_write is not None:
                before_write()
            client = self.smtp_factory(
                host=request.smtp_host,
                port=request.port,
                timeout=float(timeout_seconds),
            )
            client.login(request.credential.username, request.credential.password)
            refused = client.sendmail(
                request.envelope_sender, list(request.recipients), request.data
            )
            data_accepted = True
            if refused:
                raise SMTPTransportError("delivery_uncertain", delivery_may_have_occurred=True)
            try:
                client.quit()
            except Exception:
                pass
            client = None
            return SMTPSendResult(operation_id, "accepted")
        except SMTPTransportError:
            raise
        except TimeoutError as error:
            raise SMTPTransportError("timeout", delivery_may_have_occurred=data_accepted) from error
        except smtplib.SMTPAuthenticationError as error:
            raise SMTPTransportError("forbidden") from error
        except smtplib.SMTPRecipientsRefused as error:
            raise SMTPTransportError(
                "delivery_uncertain", delivery_may_have_occurred=True
            ) from error
        except smtplib.SMTPException as error:
            raise SMTPTransportError(
                "delivery_uncertain" if data_accepted else "unavailable",
                delivery_may_have_occurred=data_accepted,
            ) from error
        except OSError as error:
            raise SMTPTransportError(
                "timeout" if isinstance(error, TimeoutError) else "unavailable",
                delivery_may_have_occurred=data_accepted,
            ) from error
        except Exception as error:
            raise SMTPTransportError(
                "unavailable", delivery_may_have_occurred=data_accepted
            ) from error
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()
