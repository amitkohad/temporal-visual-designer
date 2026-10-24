"""Trusted field-level authorization for pinned human-task forms."""

from dataclasses import dataclass
from typing import Protocol

from nexusflow.contracts.models import Scope


@dataclass(frozen=True)
class TaskFieldAccess:
    readable_fields: frozenset[str]
    editable_fields: frozenset[str]


class TaskFieldPolicy(Protocol):
    def access(self, scope: Scope, task_id: str, actor_id: str) -> TaskFieldAccess:
        """Resolve fresh server-owned permissions; unavailable policy must deny."""
        ...
