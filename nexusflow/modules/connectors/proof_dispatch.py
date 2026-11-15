"""Single proof-dispatch owner routing HTTP JSON and email text by activity_id."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nexusflow.modules.connectors.temporal_email import EmailTextActivities
from nexusflow.modules.connectors.temporal_http import HTTPJSONActivities
from nexusflow.modules.durable_runtime.types import ActivityCall


class CombinedProofDispatch:
    """Owns ``nexusflow.proof.dispatch`` when both live HTTP and live email are enabled."""

    def __init__(
        self,
        *,
        http: HTTPJSONActivities | None = None,
        email: EmailTextActivities | None = None,
    ) -> None:
        if http is None and email is None:
            raise ValueError("Combined proof dispatch requires at least one bridge")
        self.http = http
        self.email = email

    @activity.defn(name="nexusflow.proof.dispatch")
    async def dispatch(self, call: ActivityCall) -> str:
        if type(call) is not ActivityCall or type(call.activity_id) is not str:
            raise ApplicationError(
                "Proof dispatch failed", type="ProofDispatch.invalid_request", non_retryable=True
            )
        if call.activity_id == "nexus.http.json":
            if self.http is None:
                raise ApplicationError(
                    "HTTP JSON proof dispatch is not registered",
                    type="ProofDispatch.unavailable",
                    non_retryable=True,
                )
            # Reuse the HTTP bridge body while already inside activity context.
            return await self.http.run(call)
        if call.activity_id == "nexus.email.text":
            if self.email is None:
                raise ApplicationError(
                    "Email text proof dispatch is not registered",
                    type="ProofDispatch.unavailable",
                    non_retryable=True,
                )
            return await self.email.run(call)
        raise ApplicationError(
            "Unsupported proof activity", type="ProofDispatch.forbidden", non_retryable=True
        )
