"""Request-bound adapter; every operation rechecks token and current grants."""

from uuid import uuid4

from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.service import IdentityService


class IdentityProjectAuthorization:
    def __init__(self, identity: IdentityService, token: str) -> None:
        self.identity = identity
        self.token = token

    def authorize(self, scope: Scope, permission: str, project_id: str) -> str:
        request_id = str(uuid4())
        context = self.identity.authorize(
            self.token, scope, permission, request_id=request_id, correlation_id=request_id
        )
        return context.actor.subject
