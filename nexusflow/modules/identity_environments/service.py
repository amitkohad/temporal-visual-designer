"""Token-to-context gateway with bounded local admission and no client-supplied roles."""

from collections.abc import Callable
from math import isfinite
from threading import Lock
from time import monotonic

from nexusflow.contracts.models import RequestContext, Scope
from nexusflow.modules.identity_environments.policy import AuthorizationPolicy
from nexusflow.modules.identity_environments.tokens import EntraTokenVerifier
from nexusflow.modules.identity_environments.types import (
    Environment,
    GroupResolver,
    IdentityError,
    IdentityRepository,
    Principal,
    SigningKeys,
)


class AdmissionLimiter:
    """Process-local bounded limiter; deployment-wide limiting belongs at the gateway."""

    def __init__(
        self,
        *,
        maximum: int = 120,
        window_seconds: float = 60,
        maximum_buckets: int = 1024,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            type(maximum) is not int
            or maximum <= 0
            or type(maximum_buckets) is not int
            or maximum_buckets <= 0
            or isinstance(window_seconds, bool)
            or not isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError("Admission limits must be positive")
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.maximum_buckets = maximum_buckets
        self.clock = clock
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def admit(self, organization_id: str) -> None:
        with self._lock:
            now = self.clock()
            self._buckets = {
                key: value
                for key, value in self._buckets.items()
                if now - value[0] < self.window_seconds
            }
            start, count = self._buckets.get(organization_id, (now, 0))
            if count >= self.maximum or (
                organization_id not in self._buckets and len(self._buckets) >= self.maximum_buckets
            ):
                raise IdentityError("unavailable", "Identity request limit exceeded")
            self._buckets[organization_id] = (start, count + 1)


class IdentityService:
    def __init__(
        self,
        repository: IdentityRepository,
        keys: SigningKeys,
        *,
        groups: GroupResolver | None = None,
        limiter: AdmissionLimiter | None = None,
    ) -> None:
        self.repository = repository
        self.keys = keys
        self.groups = groups
        self.limiter = limiter if limiter is not None else AdmissionLimiter()
        self.policy = AuthorizationPolicy(repository)

    def _verify(self, token: str, scope: Scope) -> Principal:
        binding = self.repository.binding(scope.organization_id)
        if binding is None:
            raise IdentityError("unauthenticated", "Invalid access token")
        self.limiter.admit(binding.organization_id)
        return EntraTokenVerifier(binding, self.keys, self.groups).verify(token)

    def authorize_user_object_id(
        self,
        token: str,
        scope: Scope,
        permission: str,
        *,
        request_id: str,
        correlation_id: str,
    ) -> str:
        """Freshly authorize a user and return the verified directory object ID."""
        principal = self._verify(token, scope)
        if principal.kind != "user":
            raise IdentityError("forbidden", "A user identity is required")
        self.policy.authorize(
            principal, scope, permission, request_id=request_id, correlation_id=correlation_id
        )
        return principal.object_id

    def authorize(
        self,
        token: str,
        scope: Scope,
        permission: str,
        *,
        request_id: str,
        correlation_id: str,
    ) -> RequestContext:
        principal = self._verify(token, scope)
        return self.policy.authorize(
            principal,
            scope,
            permission,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def placement(
        self,
        token: str,
        scope: Scope,
        *,
        request_id: str,
        correlation_id: str,
    ) -> Environment:
        principal = self._verify(token, scope)
        return self.policy.placement(
            principal, scope, request_id=request_id, correlation_id=correlation_id
        )
