"""Metadata-only owner-scoped debugger discovery responses."""

from nexusflow.contracts.models import Contract
from nexusflow.modules.designer_debugger.types import DebugSession


class DebugSessionPage(Contract):
    items: tuple[DebugSession, ...]
    next_cursor: str | None
