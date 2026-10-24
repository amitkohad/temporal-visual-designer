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
| GET | `/context` | Verify access to the selected scope |
| GET | `/schema` | Retrieve authenticated OpenAPI |
| GET | `/projects?limit=50&offset=0` | List scoped project metadata |
| POST | `/projects` | Create a Dev project |
| GET | `/projects/{id}?revision=1` | Read current or selected immutable revision |
| PUT | `/projects/{id}` | Save with expected-revision concurrency |
| POST | `/projects/{id}/validation` | Validate authoring content without saving |
| POST | `/projects/{id}/releases` | Publish a whole-project immutable release |
| GET | `/activities?query=&limit=50&offset=0` | Search readable published catalog metadata |
| GET | `/projects/{id}/activation` | Read current project activation summary |
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

Package download/import, archive streams, evidence attachments, project history/compare/restore HTTP routes, form preview/submission APIs, inbox/debug controls, live identity adapters and React UI remain separate work. Existing backend methods do not imply those routes exist. The run-status route is a safe summary and deliberately does not expose activity inputs/outputs.
