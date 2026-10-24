"""Request-bound start authorization; activation reuses IdentityProjectAuthorization."""

from nexusflow.contracts.models import Scope
from nexusflow.modules.project_versioning.authorization import IdentityProjectAuthorization


class IdentityStartAuthorization:
    def __init__(self, authorization: IdentityProjectAuthorization) -> None:
        self.authorization = authorization

    def authorize(self, scope: Scope, project_id: str) -> None:
        self.authorization.authorize(scope, "run.start", project_id)
