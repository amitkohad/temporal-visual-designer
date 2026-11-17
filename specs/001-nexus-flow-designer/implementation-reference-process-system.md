# Reference-process system suite (NF-T016a / SC-01 slice)

Branch: `feature/reference-process-system-902e`.

## Implemented boundary

Critical-path system fixtures under `nexusflow/tests/system/` drive the two business starters against **local-demo / signed local providers + owned Temporal** (no workflow-code edits):

1. **Dev lifecycle** (`test_reference_process_lifecycle.py`): for `employee-onboarding` and `purchase-requisition`, create from template → publish → activate → start → submit confirmation forms and approve each linear stage → run reaches `completed`.
2. **Package promote** (`test_reference_process_promote.py`): purchase requisition Dev export → UAT stage → materialize → explicit activate; optional Prod stage/materialize/activate with change-request evidence text and opaque attachment. Dev activation remains the original release.

Thin helpers compose existing local launcher, template acceptance and identity grant patterns. Managed human-task completion uses the existing `enable-tasks` local profile (Dev-only task worker).

Local empty configuration revision `local-v1` now validates for Dev/UAT/Prod on the installation organization so package promote readiness matches the documented Promote UI path. Extra Entra-pilot organization IDs remain Dev-only. This does not introduce destination configuration storage, Entra, or production providers.

## Verification

```sh
uv sync --locked
uv run pytest -q -m integration nexusflow/tests/system/
uv run pytest -q nexusflow/tests/local/test_bootstrap.py::test_local_configuration_validates_actual_empty_object_schema
```

## Explicitly not claimed

- Full NF-001–012 product surface (standalone Designer/Admin/task portal browser)
- SC-02 isolated suite completeness, SC-03 exhaustive cross-tenant matrix, SC-04 every concurrent activation/restart edge
- NF-T017 readiness / F10
- Live Entra, production identity, browser UI acceptance
- UAT/Prod managed-approval execution (local task worker remains Dev-scoped)
- Rollback and in-flight old-run completion matrices beyond this SC-01 slice

Parent **NF-T016** remains open for the remaining breadth above. Nested **NF-T016a** is checked for this critical-path slice only.
