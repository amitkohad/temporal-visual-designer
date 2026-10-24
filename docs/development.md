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
