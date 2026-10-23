# Feature 02 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Future implementation only. `[P]` means disjoint owned paths after listed prerequisites. Shared contract changes require coordinator review.

- [ ] **F02-T001** Freeze operation/error schemas and fixtures in `nexusflow/modules/02-project-versioning/contracts/`; reconcile shared contracts and review F02-A01–A07. Prerequisites: NF-T003; use identity/validator/catalog stubs.
- [ ] **F02-T002 [P]** Implement revision storage, save concurrency, history, compare, restore, and local unit tests in `nexusflow/modules/02-project-versioning/backend/revisions/`. Prerequisites: F02-T001. Maps: F02-A02, A04, A07.
- [ ] **F02-T003 [P]** Implement immutable release publication, digest/idempotency, validation ports, retention, and local unit tests in `nexusflow/modules/02-project-versioning/backend/releases/`. Prerequisites: F02-T001. Maps: F02-A01, A03, A05–A07. Consume revision contract through a fake until integration; do not edit revisions.
- [ ] **F02-T004 [P]** Implement project/history/compare/restore/publication screens and component tests in `nexusflow/modules/02-project-versioning/ui/`. Prerequisites: F02-T001. Maps: F02-A01, A02, A04, A05. Use a fake API.
- [ ] **F02-T005 [P]** Implement independent acceptance and crash-retry contract tests in `nexusflow/modules/02-project-versioning/tests/acceptance/`. Prerequisites: F02-T001. Maps: F02-A01–A07. Use contract fixtures and a replaceable service harness; do not edit backend or UI.
- [ ] **F02-T006** Wire revision and publication implementations, then sibling ports in `nexusflow/modules/02-project-versioning/integration/`; execute acceptance and applicable lint. Prerequisites: F02-T002–T005 and available feature 01/03/04 adapters; fake-port acceptance remains independently runnable. Maps: F02-A01–A07. Record results in `nexusflow/modules/02-project-versioning/tests/verification.md`.
