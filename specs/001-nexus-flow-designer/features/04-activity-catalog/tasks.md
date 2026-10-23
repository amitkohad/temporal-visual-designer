# Feature 04 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Future implementation only. `[P]` tasks can run concurrently after contract freeze because ownership is disjoint.

- [ ] **F04-T001** Freeze bundle/descriptor/lifecycle/error schemas, Python dispatch contract, resource-limit settings, and malicious/valid fixtures in `nexusflow/modules/04-activity-catalog/contracts/`. Coordinate orchestration compatibility with feature 05, connection resolution with feature 08 and portable export with feature 09. Prerequisites: NF-T003; use ports/fakes. Maps: F04-A01–A08.
- [ ] **F04-T002 [P]** Implement staged import, archive/schema/digest validation, cleanup, idempotency, and local unit tests in `nexusflow/modules/04-activity-catalog/backend/intake/`. Prerequisites: F04-T001. Maps: F04-A02, A04, A07.
- [ ] **F04-T003 [P]** Implement authorization, immutable publish, search/resolve, lifecycle, usage retention, and export resolution with local tests in `nexusflow/modules/04-activity-catalog/backend/catalog/`. Prerequisites: F04-T001. Maps: F04-A01, A03, A05, A06. Use intake and runtime-capability fakes.
- [ ] **F04-T004 [P]** Implement Admin import/validation/publish/lifecycle UI and component tests in `nexusflow/modules/04-activity-catalog/ui/`. Prerequisites: F04-T001. Maps: F04-A01–A06. Use a fake API; do not implement execution UI.
- [ ] **F04-T005 [P]** Implement independent security, recovery, catalog, and dependency-export acceptance tests in `nexusflow/modules/04-activity-catalog/tests/acceptance/`. Prerequisites: F04-T001. Maps: F04-A01–A07. Do not edit backend/UI paths.
- [ ] **F04-T007 [P]** Implement isolated Python worker and local sandbox tests in `nexusflow/modules/04-activity-catalog/workers/python/`. Prerequisites: F04-T001, approved Python artifact format and worker isolation decisions. Maps: F04-FR10, F04-A08. Consume frozen dispatch/connection fakes; do not edit catalog/UI paths.
- [ ] **F04-T006** Wire module, Designer catalog, project dependency, identity, Python worker and runtime-capability adapters in `nexusflow/modules/04-activity-catalog/integration/`; execute acceptance and applicable lint. Prerequisites: F04-T002–T005, F04-T007 and available sibling adapters for cross-feature tests; fake-port suite remains standalone. Maps: F04-A01–A08. Record results in `nexusflow/modules/04-activity-catalog/tests/verification.md`.
