# Feature 04 implementation plan

## Ownership and boundaries

Own future paths `nexusflow/modules/04-activity-catalog/contracts/`, `backend/intake/`, `backend/catalog/`, `workers/python/`, `ui/`, `tests/`, and `integration/`. Reference [shared contracts](../../contracts.md). This feature owns isolated Python workers; feature 08 owns connector behavior and connection resolution. Feature 09 owns project import/activation, consuming this module's dependency resolver. Feature 03 reads descriptors. Feature 01 supplies authorization. No feature needs these live adapters to test against frozen fakes.

## Design

Use a staged artifact lifecycle: upload to non-discoverable staging, validate metadata and archive boundaries, finalize digest-addressed artifacts, then commit catalog metadata. Persist idempotency state and ensure interrupted staging can be reclaimed. Publishing is a separate authorized transaction. Catalog records never claim a runtime is available merely because a schema is valid.

Freeze a language-neutral manifest with a Python-specific runtime section. Define dependency closure, offline-resolvable artifacts, and accepted formats before implementation. Prefer a prebuilt immutable artifact with locked dependencies over arbitrary installation during execution. Intake parses metadata without importing modules or running build hooks. Set explicit configurable resource limits in the manifest-validator contract and test their boundaries; final operational values belong to deployment configuration.

Maintain artifact usage references and prohibit garbage collection while releases or executions depend on them. Deprecation and withdrawal are distinct. Runtime access enforcement and emergency revocation effects require feature 05 coordination; ordinary catalog changes cannot silently break pinned runs.

The Python worker lane owns `workers/python/` and its local tests, independently from intake/catalog/UI. It consumes frozen dispatch and secret-resolution ports. Verify process/container isolation with malicious fixtures, resource/network restrictions, output schema validation, cancellation and dependency compatibility. Packaging/build format remains a pre-implementation architecture decision.

## Parallel ownership

Freeze schemas, lifecycle transitions, errors, and fixtures first. Intake, catalog service, Admin UI, and external acceptance tests then proceed independently in disjoint paths. Each backend/UI owner writes local unit/component tests. The acceptance owner owns only tests and consumes public contracts. Integrator wires intake/catalog/UI and sibling ports last; shared contracts never change unilaterally.

## Verification

Use deterministic archive fixtures for path traversal, symlinks, decompression limits, malformed manifests, duplicate version conflicts, and credentials. Test interruption before artifact finalization and metadata commit, plus response-loss retries. Verify authorization at every API boundary and that user Python is never imported. UI tests cover import status, validation feedback, publish permissions, and immutable versions. Run feature acceptance using in-memory ports with no cloud or worker. Later real artifact-storage tests are separate integration tests. Application tests use the commands and integration conventions approved during stack setup.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
