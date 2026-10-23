"""Request-bound identity adapter; imported candidates require publisher rights."""

from uuid import uuid4

from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.service import IdentityService


class IdentityCatalogAuthorization:
    def __init__(self, identity: IdentityService, token: str) -> None:
        self.identity = identity
        self.token = token

    def authorize(self, scope: Scope, permission: str) -> str:
        request = str(uuid4())
        context = self.identity.authorize(
            self.token, scope, permission, request_id=request, correlation_id=request
        )
        return context.actor.subject
