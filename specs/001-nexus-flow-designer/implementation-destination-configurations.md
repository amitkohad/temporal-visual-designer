# Destination configuration revisions increment

Branch: `codex/037-destination-configurations`, based on main `8920eef` (merged PR #34).

## Implemented boundary

An explicit host can now use `SQLiteConfigurationRepository` as both its configuration store and existing compatibility provider. The store keeps immutable, organization/environment/project-scoped non-secret JSON snapshots, canonical digests, verified actor/time and monotonic project-local sequences. Exact normalized retries return original metadata; changed values conflict. Atomic quotas, bounded metadata pagination, integrity-verified reads and additive dedicated storage support restart/recovery. No target project must exist before configuration preparation.

Administrator PUT/detail/list routes use fresh `connection.manage` and return metadata only. No role grants change. Business authors and production support still use their separate package import/activation rights. Readiness validates actual immutable values against bounded local release schemas, rejects reference resolution, returns false for missing/incompatible revisions and surfaces storage corruption/failure as unavailable. Configuring the same provider object ensures admin writes affect the values read by existing materialization and activation checks.

Values are bounded to 32 KiB, depth 16 and 1,000 aggregate container entries. The default quota is 1,000 revisions per scoped project. Configuration values remain outside packages and execution history; only the existing revision identity is pinned. No connection bindings, secrets, rotation or runtime injection are introduced. The opt-in local-demo fixed `local-v1` provider remains unchanged. See [ADR 0035](../../docs/decisions/0035-destination-configurations.md), [walkthrough](../../docs/destination-configurations.md) and [API](../../docs/api.md).

## Parallel ownership and verification

Frozen types preceded three disjoint lanes: domain/store/service and unit tests; API/backend and HTTP tests; independent signed acceptance and actual Temporal integration. The coordinator owns documentation/checklists, cross-lane review and full regression.

Focused domain tests: 15 passed. Focused signed API tests: 10 passed. Independent signed acceptance: 3 passed. New real Temporal acceptance: 1 passed. Coverage includes immutable actor-preserving retries/reopen, concurrent connections/quota, corruption, bounds, scope and administrator authorization, metadata redaction, unsupported providers, schema/ref rejection, missing configuration before target writes, then actual UAT materialization/activation/execution and history replay with stored configuration. Capability/directory fixtures remain explicit.

Full default regression: **1,423 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 44 integration tests deselected** (210.51 seconds). All **271 API regression tests** passed. Ruff lint and formatting pass across 334 Python files; strict mypy passes across 83 source files. Source distribution and wheel build successfully. Independent domain/API reviews found no blocking findings; `git diff --check` passes. Full actual Temporal runtime suite: **44 passed, 198 deselected** (231.35 seconds), including the new actual stored-configuration materialization/activation/execution/replay case.

## Remaining scope

F09-T01h/T02h/T04h/T05i and NF-T013h are checked for this independently verified backend scope. Parent promotion tasks remain open. Actual connection-resource mappings, secret references/resolution/rotation, runtime consumption of values, production storage/identity/capability providers, Admin screens and complete production deployment acceptance remain outstanding. No live Entra, cloud, browser or hosted CI acceptance is claimed.
