# Feature 02 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Implementation status is tracked below; numbered paths are ownership labels, with actual Python paths recorded in the implementation record. `[P]` means disjoint owned paths after listed prerequisites. Shared contract changes require coordinator review.

- [x] **F02-T001** Freeze operation/error schemas and fixtures in `nexusflow/modules/02-project-versioning/contracts/`; reconcile shared contracts and review F02-A01–A07. Prerequisites: NF-T003; use identity/validator/catalog stubs.
- [x] **F02-T002 [P]** Implement revision storage, save concurrency, history, compare, restore, and local unit tests in `nexusflow/modules/02-project-versioning/backend/revisions/`. Prerequisites: F02-T001. Maps: F02-A02, A04, A07.
- [x] **F02-T003 [P]** Implement immutable release publication, digest/idempotency, validation ports, retention, and local unit tests in `nexusflow/modules/02-project-versioning/backend/releases/`. Prerequisites: F02-T001. Maps: F02-A01, A03, A05–A07. Consume revision contract through a fake until integration; do not edit revisions.
- [ ] **F02-T004 [P]** Implement project/history/compare/restore/publication screens and component tests in `nexusflow/modules/02-project-versioning/ui/`. Prerequisites: F02-T001. Maps: F02-A01, A02, A04, A05. Use a fake API.
- [x] **F02-T005 [P]** Implement independent acceptance and crash-retry contract tests in `nexusflow/modules/02-project-versioning/tests/acceptance/`. Prerequisites: F02-T001. Maps: F02-A01–A07. Use contract fixtures and a replaceable service harness; do not edit backend or UI.
- [ ] **F02-T006** Wire revision and publication implementations, then sibling ports in `nexusflow/modules/02-project-versioning/integration/`; execute acceptance and applicable lint. Prerequisites: F02-T002–T005 and available feature 01/03/04 adapters; fake-port acceptance remains independently runnable. Maps: F02-A01–A07. Record results in `nexusflow/modules/02-project-versioning/tests/verification.md`.
  - [x] **F02-T006a** Compose signed HTTP project list/create/read/save/authoring-validation/publication through actual local project/catalog/identity providers; verify typed browser-facing responses and stale-revision conflicts (ADR 0012).
  - [ ] **F02-T006b** Complete project/history/compare/restore browser screens and remaining HTTP/provider/UI conformance; initial API composition does not close the full feature.

## Backend increment status

T001/T002/T003/T005 are complete at their stated local contract, backend and isolated-test boundaries. Evidence: `nexusflow/modules/project_versioning/` and `nexusflow/tests/project_versioning/` cover F02-A01–A07, including concurrent saves/publications and transaction failure/retry. Actual paths and remaining gates are recorded in [implementation-project-versioning.md](../../implementation-project-versioning.md). T004 and complete sibling-provider/UI integration in T006 remain open; those remaining tasks do not reopen the completed local tasks. The full feature remains open.
