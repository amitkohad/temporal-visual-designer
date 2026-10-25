# Development and verification

Use Python 3.12 or 3.13 and uv. The browser UI will use React; Node is not required for this Python foundation.

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pytest nexusflow/tests/test_contracts.py
uv run nexusflow-export-schemas
uv build
```

The lockfile pins resolved versions. Schema export writes deterministic, versioned JSON schemas under ignored artifacts/schemas/0.1. Python/Pydantic performs additional graph checks that portable JSON Schema cannot express; React schema validation alone cannot establish deployability.

`uv run nexusflow-check workflow <file.json>` returns JSON issues and exits 0 for valid contracts, 1 for invalid content, and 2 for file/usage errors. It never runs a workflow. Error reports contain field locations and codes, not input payloads. Workflow schemas, expression handling and graph semantics are a foundation subset; complete graph/dataflow/parallel-join checks arrive in the Designer/runtime features.

Default tests use local synthetic data and require no Azure credentials, Entra, SMTP, Temporal process or network. Keep fakes under nexusflow/tests, use fresh state per test and synchronization instead of sleeps. Frozen Pydantic fields prevent reassignment, but nested dictionaries are not deeply immutable; boundary adapters must copy snapshots. Test security failures and recovery relevant to each change. Do not infer production identity/isolation guarantees from fake-store tests.

Before implementation of a feature adapter, resolve its specific decisions in research.md. The runtime feasibility proof is implemented; unresolved scale/retention/identity deployment inputs block their affected production features rather than every local contract test.


## Real Temporal integration

Default uv run pytest excludes the integration marker. Run uv run pytest -m integration nexusflow/tests/runtime for sandbox/restart/replay tests. They start a disposable loopback server using NEXUSFLOW_TEMPORAL_CLI or temporal on PATH. Without either, the SDK downloads a dev server and needs network access. Local CLI 1.9.1 is verified and pinned in CI. Startup errors fail the suite. No cloud credentials or deployed cluster are used. See [ADR 0002](decisions/0002-runtime-proof.md).

Identity acceptance: `uv run pytest nexusflow/tests/identity`. Synthetic RSA/JWKS/directory fixtures and temporary SQLite files require no Microsoft credentials or network. See ADR 0003 for trusted bootstrap and provider boundaries.

Project versioning acceptance: `uv run pytest nexusflow/tests/project_versioning`. Export the feature schema with `uv run nexusflow-project-schema`. See [ADR 0004](decisions/0004-project-versioning.md) for local persistence, digest semantics and integration boundaries.

Authoring validation acceptance: `uv run pytest nexusflow/tests/designer_forms`. Export variable-reference bindings with `uv run nexusflow-binding-schema`. `AuthoringValidator(catalog)` can replace the baseline publication validator in `ProjectService`. See [ADR 0005](decisions/0005-authoring-validation.md); this is an offline backend adapter, and the proof runtime rejects bindings until execution support exists.

Server form validation: `uv run pytest nexusflow/tests/designer_forms/test_form_definition.py nexusflow/tests/designer_forms/test_forms.py nexusflow/tests/designer_forms/test_form_integration.py`. Export rule/preview schemas with `uv run nexusflow-form-schemas`. See [ADR 0006](decisions/0006-form-validation.md) for gateway trust, supported schemas and submission semantics. UI accessibility and live task authorization are not established by these library tests.

Activity intake acceptance: `uv run pytest nexusflow/tests/activity_catalog`. Export the source manifest schema with `uv run nexusflow-bundle-schema`. See [ADR 0007](decisions/0007-activity-bundle-intake.md) for bundle assembly, limits and private candidate semantics. No Python code is imported or executed by intake. Publication and isolated execution are separate work.

When implementation is confirmed, update its spec checkbox with evidence in the same change. Keep incomplete parent acceptance visible through checked completed subtasks and explicit unchecked remaining work; UI/provider tasks do not reopen separately completed backend tasks.

Catalog acceptance: `uv run pytest nexusflow/tests/activity_catalog`. `nexusflow-bundle-schema` exports both the manifest and catalog-entry schema. `apps.catalog_adapters.ScopedCatalog` supplies actual descriptors/pinned resolution to authoring/project services. See [ADR 0008](decisions/0008-activity-catalog.md) for mandatory capability checks, internal hold ownership and remaining production integrations.

Durable start acceptance: `uv run pytest nexusflow/tests/runtime/test_starts.py`. See [ADR 0009](decisions/0009-durable-starts.md) for immutable intents, retry delivery, required provider contracts and storage limitations. These isolated tests use real SQLite with fake authorization/release/dispatcher ports; they do not establish a deployed Temporal start gateway.

Temporal start dispatch: `uv run pytest nexusflow/tests/runtime/test_temporal_starts.py` covers the async start boundary and dispatch journal offline; `uv run pytest -m integration nexusflow/tests/runtime` also covers the real SDK/server adapter. See [ADR 0010](decisions/0010-temporal-start-dispatch.md) for conservative claim recovery, permanent receipts, destination binding and journal preservation requirements. Actual Entra/activation providers and operator recovery tooling remain open.

Project activation: `uv run pytest nexusflow/tests/package_promotion` verifies target-local activation/rollback, project pointer/audit transactions, retention, signed-token permissions and configuration/capability ports. `uv run pytest -m integration nexusflow/tests/runtime` additionally verifies real Temporal execution pinning across activation and rollback. See [ADR 0011](decisions/0011-project-activation.md) for conservative cross-provider holds and remaining transfer/configuration/UI scope. Production deployments and live Entra acceptance are not established by these tests.

Application API: `uv run pytest nexusflow/tests/api` verifies signed HTTP requests against actual local identity/SQLite providers with explicit runtime/configuration/capability fixtures. `uv run pytest -m integration nexusflow/tests/runtime` also verifies HTTP-to-real-Temporal start/status and registry namespace placement. Host the explicit `create_app(ApiBackend(...))` factory with configured providers; see [API guide](api.md). Live identity adapters and React UI remain pending; the separate opt-in local-demo launcher is described below.

Local launcher: `uv run nexusflow-local init`, `token`, `serve` and `stop` provide an explicit synthetic local-demo profile with real persistent SQLite/Temporal and safe welcome workflow. See [local guide](local-development.md). `uv run pytest nexusflow/tests/local` is isolated; the marked `tests/runtime/test_local_launcher_integration.py` uses real subprocesses and HTTP sockets and requires installed CLI/environment configuration. Default tests never start local server processes. Live identity and React remain separate work.

Human-task core: `uv run pytest nexusflow/tests/human_tasks` verifies scoped persistent any-one/all-approver decisions, immutable assignment snapshots, command retry/revision conflicts and atomic completion outbox recovery using injected authorization, directory and completion ports. See [ADR 0014](decisions/0014-human-task-core.md). These library tests do not establish inbox, form submission, live Entra or real Temporal task delivery acceptance.

Human-task inbox/API: `uv run pytest nexusflow/tests/human_tasks nexusflow/tests/api/test_tasks.py` verifies scoped assignment-filtered pages and signed HTTP task operations with actual local identity/task stores. The identity adapter resolves verified user object IDs, rechecks grants and rejects workload identities. See [ADR 0015](decisions/0015-human-task-api.md); browser and runtime task integration remain separate.

Reassignment acceptance: `uv run pytest nexusflow/tests/human_tasks/test_reassignment.py nexusflow/tests/api/test_reassignments.py` covers pre-decision admin snapshot changes, group refresh, revoked access, restart receipts, cross-command conflicts, real SQLite races and atomic audit rollback. See [ADR 0016](decisions/0016-task-reassignment.md). Directory providers remain explicit; the local launcher does not enable them.

Task forms: `uv run pytest nexusflow/tests/human_tasks/test_task_forms.py nexusflow/tests/api/test_task_forms.py` covers the real shared form engine, pinned SQLite form state, signed task authorization, fresh field policy, privacy, revision checks, approval guards and rollback. No browser, attachment or Temporal form mapping is established. See [ADR 0017](decisions/0017-task-forms.md).

Task notifications: `uv run pytest nexusflow/tests/human_tasks/test_notifications.py` verifies explicit one-shot reminder/escalation schedules, virtual due times, scoped persistence, retry/restart, stable delivery IDs, cancellation and SQLite write ordering. The trusted sink must enforce a bounded timeout, durably deduplicate notification IDs and avoid task-database reentry. This increment adds no HTTP scheduler or running background worker. See [ADR 0018](decisions/0018-task-notifications.md).

Sequential approvals: `uv run pytest nexusflow/tests/human_tasks/test_sequential.py nexusflow/tests/human_tasks/test_sequential_notifications.py nexusflow/tests/api/test_sequential.py` covers ordered any-one/all stages, atomic transitions and receipts, stage-specific authorization, shared forms and notification compatibility. Stage creation remains an internal operation; React configuration and Temporal task integration are separate. See [ADR 0019](decisions/0019-sequential-approvals.md).

Delegation: `uv run pytest nexusflow/tests/human_tasks/test_delegation.py nexusflow/tests/human_tasks/test_delegation_integration.py nexusflow/tests/api/test_delegation.py` verifies current-stage vote transfers, signed actor/policy boundaries, exact historical retries, partial-vote preservation, atomic rollback, shared forms and reminder cancellation. No recipient role grants or workflow bridge are implied. See [ADR 0020](decisions/0020-task-delegation.md).

Task completion delivery: `uv run pytest nexusflow/tests/human_tasks/test_async_completion.py nexusflow/tests/runtime/test_human_completions.py` verifies the async outbox and immutable Temporal destination journal offline. `uv run pytest -m integration nexusflow/tests/runtime/test_task_completion_integration.py` exercises real SQLite decisions and actual Temporal approval/rejection, retained acknowledgments, invocation isolation and worker restart/replay. Register `CompletionTarget` through the trusted host, then use `TemporalCompletionSink` with `TaskService.deliver_pending_async`; keep journals and backups indefinitely. This adds no public delivery endpoint or automatic task creation/scheduler. See [ADR 0021](decisions/0021-task-completion-delivery.md).

Managed task creation: `uv run pytest nexusflow/tests/runtime/test_managed_preflight.py nexusflow/tests/runtime/test_task_creation.py nexusflow/tests/runtime/test_managed_start_compatibility.py` verifies strict assignments, transactional provisioning and old permanent dispatch receipts. `uv run pytest -m integration nexusflow/tests/runtime/test_managed_tasks_integration.py` verifies automatic task creation, task-ID-bound approval/rejection, rules, retries and replay. Configure `TaskCreationActivities(tasks, journal, client, scope)` on the same destination worker with `activities=[creation.create]`; the task and journal databases must have separate resolved paths. Continue explicit async outbox dispatch through `TemporalCompletionSink`. See [ADR 0022](decisions/0022-managed-task-creation.md) for trusted host boundaries and remaining composition work.

Background completion delivery: `uv run pytest nexusflow/tests/human_tasks/test_completion_batches.py nexusflow/tests/human_tasks/test_completion_worker.py` verifies batch isolation, cursor sweeps, timeouts, cancellation and owned loop lifecycle. `uv run pytest -m integration nexusflow/tests/runtime/test_completion_worker_integration.py` verifies background approval/rejection against actual Temporal, broken-destination isolation and receipt recovery after store reopen. See [ADR 0023](decisions/0023-completion-dispatch-worker.md). The existing local launcher does not enable this worker automatically.

```python
from nexusflow.modules.human_tasks.dispatch import (
    CompletionDispatcher,
    run_completion_dispatcher,
)

dispatcher = CompletionDispatcher(tasks, scope, sink, batch_size=25, poll_interval=1.0)
async with run_completion_dispatcher(dispatcher):
    await serve_application()  # Host owns stores/client until this context exits.
```

Here `tasks` is the configured `TaskService` and `sink` is its scoped `TemporalCompletionSink`. Monitor `last_batch.failed`, pending work and `last_error_code`; individual failures remain pending and retry on later sweeps. The helper cancels and awaits the dispatcher before returning, so close stores/journals/client afterward. Fixed retry pacing and cooperative sink timeouts do not replace production recovery tooling or a durable retry schedule.

Local managed approvals: `uv run nexusflow-local enable-tasks` explicitly enables the synthetic Dev feature while the launcher is stopped. `serve` then wires signed task APIs, the managed creation activity and background delivery using the existing installation and Temporal destination. See the [walkthrough and sample project](local-development.md#enable-managed-approvals). `uv run pytest nexusflow/tests/local/test_task_profile.py nexusflow/tests/local/test_task_runtime.py nexusflow/tests/local/test_task_cli.py` verifies profile/permission preservation and lifecycle failures; `uv run pytest -m integration nexusflow/tests/runtime/test_local_tasks_integration.py` verifies actual CLI/socket approval, restart and revocation. See [ADR 0024](decisions/0024-local-managed-approvals.md). Browser rendering and production identity remain separate work.

Managed approval forms: `uv run pytest nexusflow/tests/human_tasks/test_creation_forms.py nexusflow/tests/runtime/test_managed_form_config.py nexusflow/tests/local/test_task_fields.py` verifies atomic binding, literal form configuration, backward compatibility and local field policy. `uv run pytest -m integration nexusflow/tests/runtime/test_local_forms_integration.py` verifies the example through real signed HTTP/CLI/Temporal, including validation gates and pinned state across draft edits/restart. Bind forms on the existing task transaction through `bind_task_form`; do not call a separately connected `TaskFormService.bind` from a provisioning callback. Generic hosts still require an explicit `TaskFieldPolicy`. See [ADR 0025](decisions/0025-managed-approval-forms.md).

Form input bindings: `uv run pytest nexusflow/tests/runtime/test_form_inputs.py nexusflow/tests/runtime/test_form_input_preflight.py nexusflow/tests/runtime/test_form_input_worker.py` verifies strict selectors, activation structure checks, materialized snapshots, backward compatibility and rejection of unresolved worker syntax. `uv run pytest -m integration nexusflow/tests/runtime/test_local_form_inputs_integration.py` exercises signed local publication/activation, two distinct input snapshots, idempotency conflicts, restart and actual Temporal completion. `validate_plan_structure` is activation-only and returns no plan; execution must use `compile_plan` with validated input. See [ADR 0026](decisions/0026-form-input-bindings.md).
