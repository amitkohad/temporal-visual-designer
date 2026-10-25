# Target package materialization increment

Branch: `codex/036-package-materialization`, based on main `a4f998a` (merged PR #33).

## Implemented boundary

Freshly authorized import operations can turn a staged package into a destination project revision and immutable release. Expected project revision guards replacement; zero requires an absent target project. Existing names are preserved. SQLite commits head, revision, release, permanent release hold and receipt together. Scoped exact retries preserve the first actor/time even after head changes or stage/readiness providers become unavailable. Changed request/stage identities conflict.

The destination must already contain matching validated approved activity bundles. Import never publishes or executes package Python. Integrity, proof-runtime structure, configuration and worker capability checks precede commit; conservative activity holds can remain after failed attempts. Production support uses package.import without requiring project.write or package.export. Activation remains separate and rechecks readiness; neither staging receipts nor active pointers are changed by materialization.

See [ADR 0034](../../docs/decisions/0034-package-materialization.md), [walkthrough](../../docs/package-materialization.md) and [API](../../docs/api.md). The local project database gains an additive receipt table. No dependencies or role grants change. Back up the project database together with catalog, stage and activation/execution stores.

## Parallel ownership and verification

Frozen shared interfaces preceded three parallel lanes: repository/contracts with repository tests; service/API with focused API tests; independent signed HTTP and actual Temporal acceptance. The coordinator owns docs, checklist reconciliation and full verification.

Repository and existing project-versioning regression: 76 passed, including 10 new tests. Focused API: 12 passed. Independent signed HTTP acceptance: 8 passed. New real Temporal integration: 1 passed. Cases cover atomic rollback, two-connection CAS and duplicate races, original-actor retry after restart, retained releases, approved/missing/withdrawn/mismatched/corrupt artifacts, unsupported configuration/capabilities, unavailable providers, strict input, fresh revocation, Prod policy and explicit UAT activation/execution/replay.

Final default suite: **1,395 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 43 integration tests deselected** (185.43 seconds). Full actual Temporal runtime suite: **43 passed, 198 deselected** (223.62 seconds). Ruff check and formatting pass across 325 Python files; strict mypy passes across 81 source files. Source distribution and wheel build successfully. Independent repository/service/API reviews found no blocking issues. `git diff --check` passes.

## Remaining scope

F09-T01g/T02g/T04g/T05h and NF-T013g are checked for this backend boundary; parent promotion tasks remain open. Automatic artifact transfer/publication, signature/provenance policy, production configuration/capability/storage providers, isolated custom activity execution, lifecycle cleanup, Admin UI and complete cross-environment production acceptance remain outstanding. Tests use synthetic signed identity and explicit readiness fixtures; no live Entra, hosted CI or browser acceptance is claimed.
