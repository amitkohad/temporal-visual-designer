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

The offline authoring validator adds structured branch/join checks, reusable-workflow recursion checks and descriptor-backed input mapping validation. See [implementation scope](specs/001-nexus-flow-designer/implementation-authoring-validation.md). The runtime supports the managed-form input subset described below; general activity and step-output execution bindings remain planned.

Server form validation now provides permission-filtered previews, conditional business rules and revision-checked partial submissions through a trusted gateway port. See [implementation scope](specs/001-nexus-flow-designer/implementation-form-validation.md); the React renderer and live task/start adapters remain planned.

Activity intake now validates bounded Python source bundles and stores private immutable candidates with scoped publisher authorization. See [implementation scope](specs/001-nexus-flow-designer/implementation-activity-intake.md). Palette publication and Python worker execution remain planned.

The activity catalog backend now supports explicit immutable publication, scoped palette search, deprecation/withdrawal, retained exports and protected catalog purge. See [implementation scope](specs/001-nexus-flow-designer/implementation-activity-catalog.md); Admin UI, isolated execution and automatic release/run retention integration remain planned.

An authenticated FastAPI application factory now composes project editing/publication, catalog reads, activation/rollback, workflow starts and safe execution status. See the [API route and hosting guide](docs/api.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-application-api.md). Production host/provider configuration remains explicit.

Run the local-demo API and real Temporal worker with `uv run nexusflow-local init` followed by `uv run nexusflow-local serve` (requires Temporal CLI 1.9.1). Issue a token with `uv run nexusflow-local token`, and stop with `uv run nexusflow-local stop`. See the [local testing guide](docs/local-development.md) for requests, persistent state and profile boundaries. The React browser application remains pending.

The human-task backend core now persists any-one/all-approver decisions, pinned assignment snapshots and a recoverable completion outbox. See [implementation scope](specs/001-nexus-flow-designer/implementation-human-task-core.md). The React inbox, form/task integration and runtime completion adapter remain planned.

An optional explicitly configured human-task API now provides assigned-task inbox pages, task/audit reads and approval decisions. See [implementation scope](specs/001-nexus-flow-designer/implementation-human-task-api.md). The local launcher does not enable this provider; React inbox and workflow-to-task integration remain planned.

Task-bound configurable forms now support permission-filtered preview and revision-checked submission, with approval blocked until a bound form is server-validated. See [implementation scope](specs/001-nexus-flow-designer/implementation-task-forms.md). Field policies and binding remain explicit host/internal operations; React rendering and workflow form mapping remain planned.

The internal human-task notification core now persists one-shot reminder/escalation schedules and retries with stable delivery IDs, cancelling due notifications for completed tasks or changed recipient snapshots. See [implementation scope](specs/001-nexus-flow-designer/implementation-task-notifications.md). Automatic scheduling, connector transports and workflow progression remain planned.

Human tasks now support fixed sequential approval stages, each with any-one/all-approver rules, current-stage access and final-only completion. See [implementation scope](specs/001-nexus-flow-designer/implementation-sequential-approvals.md). Conditional routing, React configuration and the workflow/task bridge remain planned.

Eligible participants can now delegate their uncast current-stage vote to a resolved user, preserving other votes and an auditable retry-safe transfer. See [implementation scope](specs/001-nexus-flow-designer/implementation-task-delegation.md). Recipient roles remain independently authorized; automatic delegation, React interactions and the workflow/task bridge remain planned.

Trusted workers can now asynchronously deliver persisted task completions to explicitly registered Temporal approval invocations, with exact run/release binding, permanent local receipts and retained-update recovery. See [implementation scope](specs/001-nexus-flow-designer/implementation-task-completion-delivery.md). The managed-task extension below adds automatic creation; delivery scheduling and local-launcher integration remain planned.

Approval nodes can now opt into automatic task creation through strict `input_mapping.human_task` assignments. An explicitly configured worker registers the completion destination before committing the task; the workflow binds the returned task ID and blocks manual approval bypass for managed steps. See [implementation scope](specs/001-nexus-flow-designer/implementation-managed-task-creation.md). Hosts still need explicit activity registration and completion dispatch; background scheduling, launcher integration, form mapping and React configuration remain planned.

Hosts can now run an explicitly owned background completion dispatcher with bounded pages, per-item timeouts, isolated failures and retry sweeps. A broken destination remains pending while later tasks can progress. See [implementation scope](specs/001-nexus-flow-designer/implementation-completion-dispatch-worker.md). Launcher/API wiring, notification scheduling and production supervision remain separate work.

The local demo now supports managed approvals end to end through HTTP. Stop the launcher, run `uv run nexusflow-local enable-tasks`, then restart `serve`. Publish the supplied [approval project](examples/local-approval-project.json), start it, and approve its task through the signed inbox/decision API; background delivery resumes Temporal. See the [walkthrough](docs/local-development.md#enable-managed-approvals) and [implementation scope](specs/001-nexus-flow-designer/implementation-local-managed-approvals.md). The feature is opt-in, uses synthetic Dev identity, and adds no React UI or live Entra adapter.

Managed approvals can now include an inline pinned form that is bound before task visibility. A valid form submission unlocks approval; read-only fields and write-only values retain server enforcement. Try the [form project](examples/local-approval-form-project.json) using the [form walkthrough](docs/local-development.md#managed-approval-forms). See [implementation scope](specs/001-nexus-flow-designer/implementation-managed-approval-forms.md); general workflow mappings and React rendering remain planned.

Managed form initial fields can now bind to required workflow input properties using restricted versioned JSON Pointer selectors. Activation checks the declarations, and starts resolve and pin literal values before task creation. Try the [input-binding project](examples/local-approval-input-project.json); see [scope and limits](specs/001-nexus-flow-designer/implementation-form-input-bindings.md). General activity/output bindings and React mapping controls remain planned.

Signed APIs now expose metadata-only project history, bounded revision comparison and append-only Dev restoration. Restoring does not activate a release or affect running workflows. See [API contracts](docs/api.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-project-history-api.md). React history screens remain planned.
