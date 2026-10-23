# Feature 04 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Implementation in progress. `[P]` tasks can run concurrently after their scope-specific contract freeze because ownership is disjoint. Completed intake work does not close publication, UI or worker tasks.

- [ ] **F04-T001** Freeze bundle/descriptor/lifecycle/error schemas, Python dispatch contract, resource-limit settings, and malicious/valid fixtures in `nexusflow/modules/04-activity-catalog/contracts/`. Coordinate orchestration compatibility with feature 05, connection resolution with feature 08 and portable export with feature 09. Prerequisites: NF-T003; use ports/fakes. Maps: F04-A01–A08.
  - [x] **F04-T001a** Freeze source-bundle manifest, intake limits/errors, logical digest and valid/malicious fixtures in `activity_catalog/types.py`, `digests.py` and isolated tests.
  - [x] **F04-T001b** Freeze catalog publication/lifecycle/error, capability, audit, usage and export ports in `catalog_types.py` and independent fixtures.
  - [ ] **F04-T001c** Freeze worker dispatch and sandbox resource/credential/network contracts with runtime/connector/promotion adapters.
- [x] **F04-T002 [P]** Implement staged import, archive/schema/digest validation, cleanup, idempotency, and local unit tests in `nexusflow/modules/04-activity-catalog/backend/intake/`. Prerequisites: F04-T001a (intake contract subset). Maps: F04-A02, A04, A07.
- [x] **F04-T003 [P]** Implement authorization, immutable publish, search/resolve, lifecycle, usage retention, and export resolution with local tests in `nexusflow/modules/04-activity-catalog/backend/catalog/`. Prerequisites: F04-T001a/b (catalog contracts). Maps: F04-A01, A03, A05, A06. Use intake and runtime-capability fakes.
- [ ] **F04-T004 [P]** Implement Admin import/validation/publish/lifecycle UI and component tests in `nexusflow/modules/04-activity-catalog/ui/`. Prerequisites: F04-T001. Maps: F04-A01–A06. Use a fake API; do not implement execution UI.
- [x] **F04-T005 [P]** Implement independent security, recovery, catalog, and dependency-export acceptance tests in `nexusflow/modules/04-activity-catalog/tests/acceptance/`. Prerequisites: F04-T001a/b. Maps: F04-A01–A07. Do not edit backend/UI paths.
  - [x] **F04-T005a** Independent intake security/recovery/identity/isolation/non-execution tests against real SQLite and fake authorization; publish-time catalog behaviors remain separate.
  - [x] **F04-T005b** Independent published catalog, lifecycle, pinned export, immutable retry, capability-failure and isolation acceptance passes against real local repositories with a fake capability provider. Executable worker acceptance is F04-T007/F04-A08, not part of T005.
- [ ] **F04-T007 [P]** Implement isolated Python worker and local sandbox tests in `nexusflow/modules/04-activity-catalog/workers/python/`. Prerequisites: F04-T001, approved Python artifact format and worker isolation decisions. Maps: F04-FR10, F04-A08. Consume frozen dispatch/connection fakes; do not edit catalog/UI paths.
- [ ] **F04-T006** Wire module, Designer catalog, project dependency, identity, Python worker and runtime-capability adapters in `nexusflow/modules/04-activity-catalog/integration/`; execute acceptance and applicable lint. Prerequisites: F04-T002–T005, F04-T007 and available sibling adapters for cross-feature tests; fake-port suite remains standalone. Maps: F04-A01–A08. Record results in `nexusflow/modules/04-activity-catalog/tests/verification.md`.

  - [x] **F04-T006a** Wire actual catalog/intake/project providers through the authoring adapter and verify signed-token palette/publisher policy.
  - [ ] **F04-T006b** Complete Admin/Designer UI, actual worker/capability providers and automatic transactional release/run retention orchestration; verify F04-A01–A08.

## Intake evidence

F04-T001a/T002/T005a are implemented in `nexusflow/modules/activity_catalog/` and `nexusflow/tests/activity_catalog/`. Validation stages only bounded bytes in memory, never extracts or executes Python, and commits private validated metadata/archive atomically. [Implementation record](../../implementation-activity-intake.md) records tests and remaining scope. The intake increment alone did not complete catalog/UI/worker work. The following catalog increment closes F04-T003/T005; full F04-T001/T004/T006/T007 remain open.

## Catalog evidence

F04-T001b/T003/T005/T006a are implemented and verified at their local backend/isolated-test boundaries. [Catalog implementation record](../../implementation-activity-catalog.md) maps actual Python paths, tests and remaining integration. Publishing checks a required capability port; fake capability tests do not establish a deployed worker. UI and isolated execution remain separate unchecked tasks.
