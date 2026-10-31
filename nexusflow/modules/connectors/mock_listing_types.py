"""Bounded private-store metadata enumeration; never fixture content."""

from dataclasses import dataclass
from typing import Protocol

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_storage_types import HTTPMockFixtureReceipt


@dataclass(frozen=True)
class HTTPMockFixtureReceiptPage:
    items: tuple[HTTPMockFixtureReceipt, ...]
    next_after_connection_id: str | None = None
    next_after_connection_revision: str | None = None


class HTTPMockFixtureListingStore(Protocol):
    def list_metadata(
        self,
        scope: Scope,
        *,
        limit: int = 25,
        after_connection_id: str | None = None,
        after_connection_revision: str | None = None,
    ) -> HTTPMockFixtureReceiptPage: ...
