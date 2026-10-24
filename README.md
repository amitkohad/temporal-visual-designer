# Nexus Flow Designer

A visual business-process designer powered by Temporal. The backend and workflow workers use Python; the browser UI will use React.

## Foundation development

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run nexusflow-export-schemas
uv build
```

Python 3.12 or 3.13 and uv are required. No Azure, Entra, database or Temporal service is needed for these tests. The foundation provides strict contract validation, portable schemas and isolated test helpers. A limited Python Temporal proof now executes linear workflows with durable approvals, timers and debug pauses. The full runtime and React UI remain in development.

See [development commands](docs/development.md), the [Python/React architecture decision](docs/decisions/0001-foundation.md), and [implementation specifications](specs/001-nexus-flow-designer/README.md).

Run the real proof with `uv run pytest -m integration nexusflow/tests/runtime`. See [runtime proof](specs/001-nexus-flow-designer/implementation-runtime-proof.md).

The identity/environment backend core now validates signed Entra tokens through injected key/directory providers, applies scoped persisted grants and maintains logical Dev/UAT/Prod routing. See [implementation scope](specs/001-nexus-flow-designer/implementation-identity-environments.md); live Microsoft adapters and React sign-in remain planned.

The project versioning backend now supports scoped revision history, restore and immutable whole-project releases. See [implementation scope](specs/001-nexus-flow-designer/implementation-project-versioning.md); React project screens and production catalog/deployment adapters remain planned.

The offline authoring validator adds structured branch/join checks, reusable-workflow recursion checks and descriptor-backed input mapping validation. See [implementation scope](specs/001-nexus-flow-designer/implementation-authoring-validation.md). Variable bindings are validated for authoring and rejected by the proof runtime until execution support is implemented.

Server form validation now provides permission-filtered previews, conditional business rules and revision-checked partial submissions through a trusted gateway port. See [implementation scope](specs/001-nexus-flow-designer/implementation-form-validation.md); the React renderer and live task/start adapters remain planned.

Activity intake now validates bounded Python source bundles and stores private immutable candidates with scoped publisher authorization. See [implementation scope](specs/001-nexus-flow-designer/implementation-activity-intake.md). Palette publication and Python worker execution remain planned.

The activity catalog backend now supports explicit immutable publication, scoped palette search, deprecation/withdrawal, retained exports and protected catalog purge. See [implementation scope](specs/001-nexus-flow-designer/implementation-activity-catalog.md); Admin UI, isolated execution and automatic release/run retention integration remain planned.

An authenticated FastAPI application factory now composes project editing/publication, catalog reads, activation/rollback, workflow starts and safe execution status. See the [API route and hosting guide](docs/api.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-application-api.md). Production host/provider configuration remains explicit.

Run the local-demo API and real Temporal worker with `uv run nexusflow-local init` followed by `uv run nexusflow-local serve` (requires Temporal CLI 1.9.1). Issue a token with `uv run nexusflow-local token`, and stop with `uv run nexusflow-local stop`. See the [local testing guide](docs/local-development.md) for requests, persistent state and profile boundaries. The React browser application remains pending.

The human-task backend core now persists any-one/all-approver decisions, pinned assignment snapshots and a recoverable completion outbox. See [implementation scope](specs/001-nexus-flow-designer/implementation-human-task-core.md). The React inbox, form/task integration and runtime completion adapter remain planned.

An optional explicitly configured human-task API now provides assigned-task inbox pages, task/audit reads and approval decisions. See [implementation scope](specs/001-nexus-flow-designer/implementation-human-task-api.md). The local launcher does not enable this provider; React inbox and workflow-to-task integration remain planned.

Task-bound configurable forms now support permission-filtered preview and revision-checked submission, with approval blocked until a bound form is server-validated. See [implementation scope](specs/001-nexus-flow-designer/implementation-task-forms.md). Field policies and binding remain explicit host/internal operations; React rendering and workflow form mapping remain planned.
