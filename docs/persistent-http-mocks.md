# Persistent HTTP mock fixtures

Explicit hosts can substitute `SQLiteHTTPMockScenarios` for static fixtures:

```python
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios

fixtures = SQLiteHTTPMockScenarios("private-dev-fixtures.sqlite3")
receipt = fixtures.put(scenario)
# Pass fixtures to HTTPMockExecutor with the existing trusted Dev policy.
# Pin the activity input to receipt.fixture_digest.
# Close fixtures after the worker has stopped using it.
```

`scenario` is an existing `HTTPMockScenario` containing synthetic data. A fixture
is immutable for its organization, environment, connection ID and revision.
Identical writes are idempotent; changed content requires another revision.
Reopening the same database preserves the existing `scenario_digest` pin.

Default quotas are 100 fixtures and 8 MiB of encoded fixture documents per scope,
configurable up to 1,000 fixtures and 64 MiB. Individual request/response limits
are at most 4 MiB each; executor limits still apply independently. Base64 encoding
counts toward the storage quota. Receipts contain identity and size metadata only.

The database holds private fixture content in plaintext and belongs to the trusted
host. This adapter has no business-user permissions or public write endpoint.
Use the existing explicit Dev policy and fresh worker authorization; rows in the
database do not grant permission. Runtime reads return independent snapshots and
fail closed on invalid or corrupted stored documents. There is no network fallback.

Explicit hosts can add [signed Dev JSON fixture APIs](http-mock-admin.md).
Designer management, default launcher registration, project packaging,
production storage operations and cloud acceptance remain separate work. See
[ADR 0056](decisions/0056-persistent-http-mocks.md).
