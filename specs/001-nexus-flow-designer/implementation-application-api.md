# Initial application API increment

Branch: `codex/014-application-api`, based on merged PR #11 (`6157106`). Decision: [ADR 0012](../../docs/decisions/0012-application-api.md). HTTP route/use guide: [docs/api.md](../../docs/api.md).

## Implemented

- `nexusflow/apps/api.py`: explicit authenticated ASGI factory, typed transport views and scoped project/catalog/activation/start/status routes.
- `api_backend.py`: request-bound identity adapters, actual module composition and Temporal gateway with registry-owned placement.
- `api_boundary.py` / `api_models.py`: bounded body buffering, safe error/correlation handling, strict finite JSON requests and typed response contracts exposed through authenticated OpenAPI.
- Project service/repository: scoped metadata pagination and authorized no-save authoring validation.
- Start repository: internal organization/environment/project-scoped execution lookup before HTTP runtime reads.
- `tests/api/`: real HTTP transport, signed-token and SQLite acceptance; body/placement injection rejection, revocation, cross-scope reads, concurrency status codes, pending recovery and response redaction.
- `tests/runtime/test_api_integration.py`: actual namespace registration and Temporal client/worker reached through signed HTTP create → publish → activate → start → status/retry requests.

## Task mapping and remaining scope

NF-T015a, F01-T004c, F02-T006a, RT-T03e/T04e and F09-T05c are complete at their specified HTTP/local-provider boundaries. Source identity checks and storage providers are actual implementations; synthetic signing keys/configuration/capability/runtime fixtures do not establish live Entra/Azure readiness. Real Temporal acceptance is separate and explicitly executed.

Full integration/UI parents remain open. This increment is the API portion of the runnable-browser milestone, not a completed browser application. A configured host module can run the ASGI factory under Uvicorn, but a turnkey local bootstrap, provider configuration, worker startup command and React app remain pending. There is no default fake identity or automatic database/tenant provisioning.

Download/stream routes, evidence handling, history/compare/restore routes, inbox/form/debug APIs and full production provider/migration/operational composition remain separate unchecked work. HTTP acceptance cannot close future route/download/stream tests. All limitations and authentication/body/status semantics are in ADR 0012 and the route guide.

## Verification

Verified locally on Python 3.12.2:

- `uv run pytest nexusflow/tests/api -q`: 19 passed.
- `uv run pytest -q`: 552 passed, 10 integration tests deselected.
- `uv run pytest -m integration nexusflow/tests/runtime -q`: 10 passed against disposable local Temporal, including the signed HTTP-to-Temporal route test.
- Ruff lint/format checks passed; strict mypy passed for 58 source files.
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

Hosted CI has not been run for this uncommitted branch. Local ASGI transport tests verify the application HTTP boundary; they do not claim production network hosting, TLS, cloud identity or React browser acceptance. The real integration suite explicitly verifies the actual Temporal gateway separately from runtime fixtures used by default tests.
