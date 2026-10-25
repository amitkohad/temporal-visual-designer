# Initial application API

The ASGI application factory is `nexusflow.apps.api.create_app`. It requires an `ApiBackend` containing explicitly configured providers; no default credentials, environments or fake workers are installed. Provider lifecycle remains the caller's responsibility. See [ADR 0012](decisions/0012-application-api.md).

To host it, supply a Python module (for example `your_host.py`) which constructs the configured backend and assigns `app = create_app(backend)`, then run:

```sh
uv run uvicorn your_host:app --host 127.0.0.1 --port 8000
```

`your_host.py` is a host composition example, not a supplied default production module. For an immediately runnable local-demo API and real worker, use the [local launcher guide](local-development.md). Do not run `--factory nexusflow.apps.api:create_app` without providing its required backend. Tests below also provide an isolated application harness.

`ApiBackend` requires:

- `IdentityService` with trusted repository, signing-key provider and applicable directory/group provider.
- Project, catalog, intake, activation and start repositories.
- Explicit destination worker capabilities and immutable configuration compatibility providers.
- A `RuntimeGateway`; `TemporalRuntimeGateway` takes an async, server-owned client provider and a permanent SQLite dispatch journal. Client namespace must match trusted registry placement.

All routes below are relative to `/api/v1/organizations/{organization_id}/environments/{environment_id}` and require `Authorization: Bearer <access-token>`. IDs follow the shared identifier contract. Browser API requests cannot supply role grants or Temporal destinations.

| Method | Path | Operation |
|---|---|---|
| GET | `/tasks?status=pending&run_id=run-1&limit=50&offset=0` | List snapshot-assigned tasks (status/run filters optional) |
| GET | `/tasks/{id}` | Read an eligible task |
| GET | `/tasks/{id}/audit` | Read eligible task audit |
| GET | `/tasks/{id}/form` | Preview a pinned form with permitted fields and values |
| POST | `/tasks/{id}/form` | Validate and persist form values using the task revision |
| POST | `/tasks/{id}/decisions` | Approve/reject with command ID and expected revision |
| POST | `/tasks/{id}/delegations` | Transfer an eligible uncast vote to a resolved user |
| POST | `/tasks/{id}/reassignments` | Replace or refresh pending assignments before voting (scoped admin) |
| GET | `/context` | Verify access to the selected scope |
| GET | `/schema` | Retrieve authenticated OpenAPI |
| GET | `/projects?limit=50&offset=0` | List scoped project metadata |
| GET | `/project-templates` | List built-in business-process template metadata |
| GET | `/project-templates/{id}` | Read template version, assignment slots and input schema |
| POST | `/project-templates/{id}/projects` | Create an independently editable Dev project draft |
| POST | `/projects` | Create a Dev project |
| GET | `/projects/{id}?revision=1` | Read current or selected immutable revision |
| GET | `/projects/{id}/history?limit=50&before_revision=10` | Page revision metadata, newest first |
| GET | `/projects/{id}/compare?before_revision=1&after_revision=2&include_layout=false` | Bounded structural comparison |
| POST | `/projects/{id}/restore` | Append a historical snapshot as a new Dev revision |
| PUT | `/projects/{id}` | Save with expected-revision concurrency |
| POST | `/projects/{id}/validation` | Validate authoring content without saving |
| POST | `/projects/{id}/releases` | Publish a whole-project immutable release |
| GET | `/activities?query=&limit=50&offset=0` | Search readable published catalog metadata |
| POST | `/activity-imports` | Import a private Python source ZIP, returning metadata |
| GET | `/activity-imports/{digest}` | Read authorized private import metadata |
| GET | `/activity-imports/{digest}/archive` | Download the original private import ZIP |
| POST | `/activities/publish` | Publish a validated import after capability approval |
| POST | `/activities/resolve` | Read lifecycle metadata for a complete pinned activity reference |
| POST | `/activities/transitions` | Deprecate or withdraw with expected-generation concurrency |
| GET | `/projects/{id}/activation` | Read current project activation summary |
| GET | `/projects/{id}/activations?limit=50&before_revision=10` | Page successful deployment summaries, newest first |
| GET | `/projects/{id}/activations/{operation_id}` | Read one immutable deployment summary and optional evidence |
| PUT | `/projects/{id}/activations/{operation_id}/attachments/{attachment_id}` | Upload an optional immutable evidence file |
| GET | `/projects/{id}/activations/{operation_id}/attachments` | List private file metadata |
| GET | `/projects/{id}/activations/{operation_id}/attachments/{attachment_id}` | Download verified opaque evidence bytes |
| POST | `/projects/{id}/activation` | Activate or roll back using `ActivationRequest` |
| POST | `/projects/{id}/executions` | Start an active workflow with an idempotency key |
| GET | `/projects/{id}/executions/{execution}` | Read scoped safe proof-runtime status |

`GET /health` is public process liveness. It does not check dependencies. Swagger UI is not exposed. CORS and browser login are not configured by this factory.

Create accepts `project_id`, `name` and a project `content` object. Save accepts `expected_revision`, `summary` and `content`. Validation accepts only `content`. Publish accepts `source_revision` and `idempotency_key`. Activation accepts the existing strict activation/rollback request and requires its project ID to match the path. Start accepts `workflow_id`, `idempotency_key`, optional `input` object and optional `source` (`manual`/`api`). Typed request/response definitions are in OpenAPI and `api_models.py`.

Create/publish return 201; accepted starts return 202 with execution/release/configuration identifiers, not business payloads. Activation receipts are summaries. Repeating an identical start or activation request retains domain idempotency; 201/202 describe the operation contract and do not imply another release/run was created. Persist the same idempotency key when retrying after an unavailable response. Known pending execution IDs can return `dispatch_pending`; no administrative recovery endpoint is implemented.

Errors return `schema_version`, `request_id`, `code`, `message`. `X-Request-ID` matches the server-generated correlation; responses use `Cache-Control: no-store`. Authentication errors return 401 and a bearer challenge; missing permission 403; scoped missing resources 404; stale/conflicting requests 409; invalid fields/content 422; unavailable providers 503. The request limit is 6 MB by default and returns 413; unsupported encoded bodies return 415. Unexpected exceptions return sanitized 500 responses. No validation response echoes submitted secrets/tokens.

Tests:

```sh
uv run pytest nexusflow/tests/api
uv run pytest -m integration nexusflow/tests/runtime
```

Package download/import, archive streams, remaining form API integrations, browser inbox/debug controls, live identity adapters and React UI remain separate work. Existing backend methods do not imply those routes exist. The run-status route is a safe summary and deliberately does not expose activity inputs/outputs.

Human-task routes require explicit `ApiBackend.tasks_database` configuration, a scoped `task_participant` grant and the verified user object ID in the pinned eligible set. The optional provider defaults to unavailable; the local launcher enables it only after the explicit `enable-tasks` command (see [local walkthrough](local-development.md#enable-managed-approvals)). Inbox responses contain `items` and `next_offset`; limit is 1–100 and offset 0–10000. Ordering is creation time then task ID; offset pages reflect current state. Decision bodies contain only `command_id`, `expected_revision` and `decision` (`approve`/`reject`). Creation and outbox delivery are internal operations without HTTP routes. See [ADR 0015](decisions/0015-human-task-api.md).

Reassignment requires scoped `task.reassign` permission (organization administrators) and explicit `ApiBackend.task_assignments` provider configuration. Send `command_id`, `expected_revision`, `user_ids` and/or `group_ids` as JSON arrays. A new command resolves a fresh complete snapshot and increments the task revision, even when refreshing unchanged declarations. Reassignment is restricted to pending tasks with no votes, bounded to 100 changes; exact retries return historical receipts without directory resolution. Former eligible users immediately lose inbox/read/decision access. Creation inputs remain immutable; assignment history and audit expose before/after snapshots to authorized readers. Missing/failed resolvers return sanitized 503 responses. Administrative reassignment does not rewrite cast votes; participant delegation uses the separate endpoint below. See [ADR 0016](decisions/0016-task-reassignment.md).

Task forms require explicit `ApiBackend.task_fields` configuration and a pre-bound internal form snapshot. GET returns the existing `FormView` structure: task `revision`, permitted `fields` metadata and filtered `values_json`. Write-only values are omitted. POST accepts only `expected_revision` and a `values` object, returning the new task `revision`. The server validates merged values and rechecks field policy at commit. Form binding and values are separate from ordinary task views/audits; binding has no HTTP route. Approval is blocked until a bound form has a successful submission; rejection remains allowed. Form edits stop after the first vote. Submissions have revision-conflict semantics: after a lost response, reload instead of assuming an idempotent command receipt. These endpoints retain safe generic error envelopes; richer field-error presentation and React rendering remain future UI work. See [ADR 0017](decisions/0017-task-forms.md).

Sequential approval tasks expose `stage_assignments` and zero-based `stage_index` in task views. Only current-stage approvers can read, list or submit new decisions. The existing decision endpoint atomically advances to the next stage, retaining `pending` status until final approval; a rejection terminates immediately. Decision records and stage-transition audits carry stage IDs. The same user may vote in multiple stages with different command IDs. A prior-stage actor with fresh `task.respond` permission can retry their own exact accepted command for its historical response, but cannot read current task state or submit a new vote unless currently eligible. One pinned form is shared and becomes read-only after the first vote. See [ADR 0019](decisions/0019-sequential-approvals.md).

Delegation accepts only `command_id`, `expected_revision` and `delegate_id`. It requires current assignment eligibility, fresh `task.respond` authorization and explicit `ApiBackend.task_assignments` resolution. Targets must be different, unassigned current-stage users; no role or membership is granted. Other votes remain unchanged. Sequential tasks update only the active stage; target users still need independent scope permissions. Delegators lose current read/form/vote access but can retry their own exact accepted command for a historical response while freshly authorized. Delegation and admin reassignment share a 100-change limit. The existing form freeze and stale-reminder cancellation rules still apply. See [ADR 0020](decisions/0020-task-delegation.md).

Project history requires fresh `project.read` permission and returns metadata only: `project_id`, `revisions` (revision, parent_revision, summary, author_id, created_at) and `next_before_revision`. Limit defaults to 50 (1–100). The positive signed-int64 cursor is exclusive; concurrent saves do not shift older pages. Read/compare revision numbers have the same int64 bounds.

Comparison requires two revision numbers and defaults to excluding top-level layout. Changes contain JSON Pointer `path`, `before_json` and `after_json`: absent values are null, while an existing JSON null is the string `"null"`. Arrays are compared atomically. Responses stop at 1,000 changes or 262,144 UTF-8 bytes of encoded change objects and return sanitized 422 without partial results when exceeded.

Restore accepts only `source_revision`, `expected_revision` and a nonblank `summary` (at most 1,000 characters). It requires fresh Dev `project.write`, appends a new revision and returns 201. It preserves history, release snapshots, activation and running executions. It does not publish or activate. Stale or duplicate retries return 409; after a lost response, reload the head before retrying. See [ADR 0027](decisions/0027-project-history-api.md).

Activity administration uses the existing scoped `activity_publisher` role; no new grants are implied. Import, private metadata and archive download require fresh `activity.import`. Publication and lifecycle transitions require `activity.publish`; pinned catalog lookup requires `activity.read`. The local demo profile does not grant publisher rights by default.

Import sends raw ZIP bytes with `Content-Type: application/zip`, without multipart or base64. The response is 201 with `digest`, normalized `manifest`, `state` (`validated`), `author_id` and `created_at`. Validation never executes or installs Python. The default HTTP body ceiling is 6,000,000 bytes, below the domain archive maximum of 8 MiB; hosts must account for both limits. Encoded bodies and unsupported media types return 415, oversized HTTP bodies 413, invalid archives 422. Metadata/download responses are private and never expose another scope's artifacts. Archive download returns the first accepted original bytes, `application/zip`, fixed attachment filename `activity-bundle.zip`, and `Cache-Control: no-store`.

Publish accepts only `{"artifact_digest":"sha256:..."}` and returns 201 with the existing `CatalogEntry` structure. It requires an explicitly configured compatible worker-capability provider; metadata validation alone does not establish worker execution. Candidates remain absent from the palette until publication. Resolve accepts an `ActivityRef` object (`activity_id`, `version`, `artifact_digest`) and returns catalog metadata including lifecycle state, even for withdrawn versions. This inspection route uses `CatalogService.get`; the domain new-use resolver retains its withdrawal checks.

Transitions accept only `reference` (a complete `ActivityRef`), `target` (`deprecated` or `withdrawn`) and positive signed-int64 `expected_generation`. They return the updated catalog entry. Wrong generations for a new transition return 409; backward transitions return 422. Repeating a transition whose target is already the current state returns the existing entry without adding audit events, as specified by the domain contract. All retries require fresh permission. Identical logical imports retain the original archive/actor, and repeated publication retains the original catalog entry. There are no source execution, deletion, arbitrary retention or published-package export endpoints in this increment. See [ADR 0028](decisions/0028-activity-admin-api.md).

Template metadata requires fresh `project.read`. Creation requires fresh `project.write` and Dev, accepts only `project_id`, `name` and `assignments`, and returns the ordinary project revision with 201. Assignments must match every declared slot exactly; each accepts `rule` (`any_one` or `all_approvers`), `user_ids` and `group_ids`, with at least one principal. No roles or memberships are granted. The built-ins `employee-onboarding` and `purchase-requisition` create fixed approval sequences with pinned read-only request context and required form confirmation. Creation does not publish, activate or execute. A duplicate project ID returns 409; reload after an ambiguous response. See the [template walkthrough](business-process-templates.md) and [ADR 0029](decisions/0029-business-process-templates.md). React template selection, custom descriptor-backed templates, conditional routing and external business actions remain separate work.

Activation/rollback bodies may include optional `evidence`, for example `{"change_request_number":"CHG-1042","notes":"Offline approval recorded"}`. Both text fields are optional/nullable; supplied strings must be nonblank and are bounded to 128 and 2,000 characters respectively. Evidence is stored with the successful operation and returned with its receipt, current activation and historical summary. Omitted/null evidence preserves legacy request fingerprints and response shapes. A supplied empty object is permitted and differs from absent evidence for retry identity. Changing evidence while reusing an operation ID returns 409; historical evidence cannot be edited through these routes. Evidence is never required for authorized Prod activation and is not checked against an external change system.

Deployment-history and operation-detail reads require fresh scoped `run.read`. History returns `project_id`, `items` and `next_before_revision`; each item contains operation/release identifiers, activation revision, action, previous release, runtime/configuration revision, checksum, actor, timestamp and optional evidence. It contains no project snapshots or workflow inputs/outputs. Limit is 1–100 (default 50); `before_revision` is an exclusive positive signed-int64 cursor. Concurrent deployments do not shift older pages. No successful operations yields an empty history even for a missing project in that scope; a missing operation returns 404. Failed attempts remain in the existing internal audit store and are excluded from this successful-operation history. See [deployment traceability](deployment-traceability.md) and [ADR 0030](decisions/0030-deployment-traceability.md). Admin UI remains separate work.

Deployment attachments are separate optional uploads after a successful operation. PUT requires fresh `package.activate`, raw `application/octet-stream` bytes and an optional `display_name` query label (default `Approval evidence`, nonblank printable text up to 128 characters). Files must contain 1–2,097,152 bytes; each operation permits 10 files. The global 6,000,000-byte transport buffer ceiling remains unchanged. Responses are 201 metadata (`attachment_id`, `display_name`, `checksum`, `size`, `actor_id`, `created_at`); exact same-ID/bytes/label retries retain the original metadata even at quota, changed uploads return 409, oversized bytes 413, unsupported media 415, and invalid empty bodies 422. Missing successful operations return 404. Uploads do not change activation receipts, release checksums or execution plans.

List/download require fresh scoped `run.read`. Listing returns a bounded metadata array without blobs. Download verifies size/SHA-256 and returns bytes as `application/octet-stream` with fixed attachment filename `deployment-evidence.bin`, `X-Content-Type-Options: nosniff`, no-store and correlation headers. Corrupt/unavailable storage returns sanitized 503. No files are rendered, extracted or executed, and no delete/edit or range/stream endpoint is provided. See the [file walkthrough](deployment-attachments.md) and [ADR 0031](decisions/0031-deployment-attachments.md). Production attachment providers and scanning policy remain separate work.
