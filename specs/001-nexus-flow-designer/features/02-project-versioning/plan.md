# Feature 02 implementation plan

## Ownership and interfaces

Own future paths `nexusflow/modules/02-project-versioning/contracts/`, `nexusflow/modules/02-project-versioning/backend/`, `nexusflow/modules/02-project-versioning/ui/`, and `nexusflow/modules/02-project-versioning/tests/`. No agent edits another feature's implementation. Follow [shared contracts](../../contracts.md); raise contract changes to the coordinator rather than editing shared contracts during parallel work.

Expose project create/read, save revision with expected revision, history/compare/restore, publish, and release read operations. Errors distinguish forbidden, conflict, invalid content, missing dependency, and referenced release. Identity is trusted only through feature 01's authorization port. Validators from feature 03 and activity lookup from feature 04 are replaceable ports. Feature 09 consumes frozen release snapshots; no activation logic lives here.

## Design

Use immutable revision records and transactional publication metadata. Blob staging is not publication: only committed manifests are discoverable. Canonical release serialization excludes authoring canvas placement from executable digest while retaining it as project authoring content; the package manifest explicitly distinguishes executable and authoring digests. Never silently drop authored resources. Resolve the full pinned dependency closure before publication. Keep a durable idempotency record with the publication transaction.

## Parallel work and dependency gates

First freeze feature-local contracts and deterministic fixtures. Backend, UI, and independently owned acceptance tests can then proceed concurrently. Backend owns backend-local unit tests, UI owns UI-local component tests, acceptance owner owns tests only. Use fake authorization, validators, artifact catalog, and release-reference lookup until sibling adapters exist. Integration is the final serial task after all lanes pass. An external dependency delays adapter integration, not independent feature verification.

## Validation

Test authorization, optimistic concurrency, structural diff/restore, canonicalization, idempotency, validation failures, reference retention, and transaction-failure recovery. Use deterministic failure injection around publication commit, not sleep-based races. Component tests verify actionable conflicts and validation messages. The feature acceptance suite runs with an in-memory store and no network. Later adapter tests use actual persistence; application tests follow the testing guide and commands selected during stack setup. Run applicable lint and the application lint command selected during stack setup after future code changes. Performance budgets await the platform sizing decision; correctness is not conditional on a guessed load target.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
