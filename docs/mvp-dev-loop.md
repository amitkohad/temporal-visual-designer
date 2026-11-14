# MVP Dev-loop contracts (Phase 0 freeze)

Frozen shared interfaces for the business-user Dev closed loop so React lanes
can proceed in parallel. Full program context lives with the product finish
plan; this file is the **in-repo source of truth** for UI agents.

Normative route and error behavior remains in [api.md](api.md). Connected
layout limits remain in [react-designer.md](react-designer.md). Starter
templates: [business-process-templates.md](business-process-templates.md).
Local host: [local-development.md](local-development.md). Do not treat
local-demo or fake-port suites as Entra or production acceptance
([AGENTS.md](../AGENTS.md)).

## Supported node set

Browser MVP editing is limited to workflow schema `0.1` graphs with 2–50
nodes, at most 200 edges, and no `error_routes`. Supported wire kinds:

| Wire kind | Visual kind | MVP mutation |
|---|---|---|
| `start` | `start` | Required; not inserted mid-chain |
| `end` | `end` | Required terminal |
| `approval` | `approval` | Insert, delete, reorder, labels |
| `decision` | `decision` | Labels; preserve edge `source_port` labels |
| `wait` | `timer` | Insert, delete, reorder, labels |
| `set_data` | `update` | Present / relabel in MVP |
| `child_workflow` | `subworkflow` | Present / relabel in MVP |

Other kinds fail closed. More than 16 nodes require complete existing designer
positions. Reject `.` / `..` identifiers and JSON numbers that JavaScript cannot
round-trip without changing executable digests.

Pilot templates: `employee-onboarding`, `purchase-requisition`.

## Layout versus executable content

- **Layout-only** writes go to
  `content.layout.nexus_flow_designer` and must not change executable digests
  (existing connected save behavior).
- **Executable** edits change `content.workflows[]` (and only necessary sibling
  fields). Save with `PUT /projects/{id}` using `expected_revision` and a full
  `content` snapshot. Preserve unknown keys.
- Call `POST /projects/{id}/validation` before publish; block publish on
  located authoring errors.
- Stable `node_id` values are retained across cosmetic edits; inserts allocate
  new IDs.

## Golden path

Base:
`/api/v1/organizations/{organization_id}/environments/{environment_id}`
with `Authorization: Bearer <token>`. Pilot defaults: organization
`local-demo`, environment `dev`.

1. `GET /project-templates` → `POST /project-templates/{id}/projects`
   (`project_id`, `name`, `assignments`)
2. `GET` / `PUT /projects/{id}` (revise); `POST /projects/{id}/validation`
3. `POST /projects/{id}/releases` (`source_revision`, `idempotency_key`)
4. `POST /projects/{id}/activation`
   (`project_id`, `release_id`, `configuration_revision`, `operation_id`,
   `expected_revision`, default `runtime_version` `proof.v1`, `action`)
5. `POST /projects/{id}/executions`
   (`workflow_id`, `idempotency_key`, `input`) →
   `GET /projects/{id}/executions/{execution}`
6. `GET /tasks?status=pending&run_id=…` → form `GET`/`POST` →
   `POST /tasks/{id}/decisions` (`command_id`, `expected_revision`,
   `decision`)

Local-demo preparation: `nexusflow-local init`, `enable-tasks`, `serve`,
`token`. Activation examples commonly use `configuration_revision` `local-v1`.

## Pilot identity

**First pilot uses the local-demo bearer token** pasted into the Connect form
(`uv run nexusflow-local token`). Entra browser sign-in is deferred; it does
not block MVP UI composition.

Shared client rules match `web/src/project-api.ts`: same-origin only, memory
token, no-store, bounded JSON, fixed private error codes, explicit idempotency
keys / operation IDs on publish, activate, and start retries.

## Phase 1 disjoint writable paths

Read any path. Write only the paths listed for the assigned lane. Coordinator
owns `web/src/App.tsx`, `web/src/main.tsx`, dependency lockfiles, and Vite
proxy configuration. Agents must not run concurrent package sync in the shared
environment.

### 1A — Executable Designer

- `web/src/workflow-edit.ts`, `web/src/workflow-edit.test.ts`
- `web/src/template-api.ts`, `web/src/template-api.test.ts`
- `web/src/project-projection.ts`, `web/src/project-projection.test.ts`
- `web/src/Projects.tsx`, `web/src/projects.css`
- `web/tests/Projects.test.tsx`, `web/e2e/projects.spec.ts`,
  `web/tests/project-fixtures.ts`

Consumes existing `web/src/project-api.ts` for list/read/save. Does not own
publish/activate/start or task UI. May import 1C control exports.

### 1B — Task portal

- `web/src/task-api.ts`, `web/src/task-api.test.ts`
- `web/src/Tasks.tsx`, `web/src/tasks.css`
- `web/tests/Tasks.test.tsx`, `web/e2e/tasks.spec.ts`
- optional `web/src/assets/tasks/`

Does not edit Designer or release-control files. App mount is coordinator-owned.

### 1C — Publish / activate / start

- `web/src/release-api.ts`, `web/src/release-api.test.ts`
- `web/src/ReleaseControls.tsx`, `web/src/release-controls.css`
- `web/tests/ReleaseControls.test.tsx`, `web/e2e/release-controls.spec.ts`

Export a stable control surface for Designer/App integration (connection,
project id, head revision, workflow id, disable flags, started/activated
callbacks). Include a deep-link affordance to the task inbox by `run_id`.

## Out of MVP

Custom activities, general expressions, form builder, live HTTP/email effects,
Admin/catalog/promote UI, debugger UI, Entra, and F10 ops. No new backend
routes unless a real gap is escalated to the coordinator.
