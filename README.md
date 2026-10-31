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

Scoped activity publishers can now import and download private Python source bundles, publish after capability approval, inspect pinned catalog metadata, and deprecate or withdraw versions through signed APIs. See [API contracts](docs/api.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-activity-admin-api.md). Admin screens and custom Python execution remain planned.

Business authors can create independent Dev drafts from employee-onboarding and purchase-requisition starter templates through signed APIs. Each uses explicit approver assignments, fixed approval steps and input-backed confirmation forms. See the [template walkthrough](docs/business-process-templates.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-business-process-templates.md). React selection, conditional routing and external provisioning/purchasing remain planned.

Deployments can now retain optional change-request numbers and notes, with authenticated paginated history and immutable operation lookup. Evidence remains optional for Prod and approval stays offline. See [deployment traceability](docs/deployment-traceability.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-deployment-traceability.md). Admin screens remain planned.

Successful deployment operations now support optional private approval files with bounded uploads, immutable retry IDs, metadata listing and checksum-verified binary downloads. See the [file walkthrough](docs/deployment-attachments.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-deployment-attachments.md). Uploads do not block or alter activation; production attachment providers and Admin screens remain planned.

Published releases can now be exported as whole-project ZIPs with exact activity bundles and inspected through a non-activating validation API. See [project packages](docs/project-packages.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-project-package-export.md). See the separate materialization step below for destination releases and readiness checks; production trust/provider integration remains planned.

Authorized users can now stage those packages durably in a target environment, retain immutable import receipts, and recover the first archive across retries/restarts. See [package staging](docs/package-import-staging.md) and [implementation evidence](specs/001-nexus-flow-designer/implementation-project-package-intake.md). Staged packages remain separate from executable target releases.

Authorized importers can now [materialize staged packages](docs/package-materialization.md) into atomic target project revisions and immutable releases after approved-artifact and readiness checks. Activation remains an explicit admin operation. See [implementation evidence](specs/001-nexus-flow-designer/implementation-package-materialization.md). Automatic artifact publication, production providers and Admin screens remain planned.

Explicit hosts can now [persist destination configuration revisions](docs/destination-configurations.md) and administer them through signed metadata-only APIs. Materialization and activation validate their actual stored values against the release schema. Configuration is immutable and outside exported packages; connection/secret resolution and Admin screens remain planned.

Explicit hosts can now expose [Dev debugger session APIs](docs/designer-debug-sessions.md) for immutable draft snapshots, [editable paused breakpoints](docs/debug-breakpoints.md), payload-free status, step-over, continue, [safe-boundary cancellation](docs/debug-session-cancellation.md) and [explicit reruns](docs/debug-rerun.md) through the sequential proof runtime. Sessions and command receipts survive retries/restarts. The React debugger panel, general runtime variable inspection, managed-task/active-activity cancellation and parallel stepping remain planned.

The standalone local launcher now supports opt-in debugging with `uv run nexusflow-local enable-debugger`. [Try the seeded workflow](docs/local-development.md#enable-dev-debugging) through the signed API, pause/step it and recover the same session after restart. Existing local installations remain disabled until enabled.

Dev sessions support [redacted frozen workflow-input inspection](docs/debug-input-inspection.md). Only explicitly public primitive fields are shown; protected/unclassified values stay hidden and draft edits cannot widen an old session. Completed activity outputs and frozen literal activity inputs are available through the separately authorized [output](docs/debug-step-outputs.md) and [step input](docs/debug-step-inputs.md) inspectors; general variables, dynamic inputs and browser controls remain planned.

Dev debugger clients can [discover their saved sessions](docs/debug-session-discovery.md) through an owner-scoped paginated metadata API, including after restart. Inputs and plans stay private; authoritative runtime status remains a separate query.

The frozen-input inspector supports [explicitly opted-in business arrays](docs/debug-array-inspection.md), including public fields of purchase line items, with protected-field redaction and bounded, position-preserving output. Previously unsupported arrays stay hidden unless a new schema revision adds the array-specific marker.

Dev sessions now expose [ordered metadata events](docs/debug-events.md) with bounded pagination, restart replay and retention-gap detection. Event values and error messages are absent. An [atomic reconnect baseline](docs/debug-reconnect.md) now pairs status, breakpoints and replay cursor; live streaming and React consumers remain planned.

Explicit hosts can configure an [environment-scoped HTTP connection registry](docs/http-connections.md) with immutable revisions, opaque credential references and signed admin metadata APIs. Secret resolution, default launcher wiring and UI remain planned.

The connector foundation includes [worker HTTP destination preflight](docs/http-destination-preflight.md): scoped authorization, pinned connection revisions, origin/IP checks, redirects and bounded DNS-provider waits. An internal [pinned HTTPS transport](docs/http-pinned-transport.md) adds verified TLS and bounded HTTP/1.1 framing. The [credential-free attempt coordinator](docs/http-attempt-execution.md) composes these with fresh authorization before DNS, after DNS and immediately before writing. Credential resolution, durable retries and workflow/UI integration remain planned.

Hosts can explicitly select [deterministic Dev HTTP mocks](docs/dev-http-mocks.md)
with fresh scoped permission, trusted Dev eligibility and digest-pinned immutable
fixtures. Mocks make no network calls and never substitute automatically for live
execution. Explicit hosts can use [persistent private SQLite fixtures](docs/persistent-http-mocks.md)
with immutable revisions, restart-stable pins and scoped quotas. Browser fixture
management, executable activity publication and workflow/UI wiring remain planned.

An internal [HTTP JSON activity](docs/http-json-activity.md) now adds typed literal
authoring metadata and explicit live/mock mapping with status-only outputs.
Its content digest identifies contract metadata; executable publication, runtime
capability registration, general bindings and palette/UI wiring remain planned.

An explicitly composed [Temporal HTTP mock worker](docs/temporal-http-mocks.md)
can now run mock steps through the proof workflow with trusted SDK context,
run-bound operation IDs, debug pause/restart and replay. Live HTTP dispatch,
default launcher integration and built-in project publication remain blocked.

Explicitly enabled Dev hosts can administer immutable JSON mock fixtures through
[signed fixture APIs](docs/http-mock-admin.md), with administrator permission,
metadata-only responses and existing persistent storage. Browser management,
fixture package transfer and default launcher enablement remain planned.
