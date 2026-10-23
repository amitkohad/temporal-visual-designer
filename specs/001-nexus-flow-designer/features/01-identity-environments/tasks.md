# Feature 01 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Backend core implementation started 2026-10-23; see ../../implementation-identity-environments.md. `[P]` lanes require disjoint paths and F01-T001 completion. NF-T003 is the program prerequisite; exact toolchain/test commands must already be selected.

- [x] F01-T001 Freeze C01/C10 fixtures, role matrix and scoped route inventory in `nexusflow/modules/01-identity-environments/contract/`. Prerequisite: NF-T003. Owner: feature lead. Acceptance: F01-A01–07 have deterministic fixtures; shared contract owner accepts any amendments.
- [x] F01-T002 [P] Implement token verification, directory lookup, grant policy and environment registry in `nexusflow/modules/01-identity-environments/backend/`. Prerequisite: F01-T001. Owner: backend agent. Acceptance: F01-A01–07 provider behavior.
- [ ] F01-T003 [P] Implement environment selection and role administration in `nexusflow/modules/01-identity-environments/ui/` against frozen fixtures. Prerequisite: F01-T001. Owner: UI agent. Acceptance: F01-A01/04/06 and accessible permission feedback.
- [ ] F01-T004 [P] Implement independent policy/token/directory fixtures and acceptance suites in `nexusflow/modules/01-identity-environments/tests/`. Prerequisite: F01-T001. Owner: test agent. Acceptance: F01-A01–07, includes cross-tenant streams/downloads and directory failure.
  - [x] F01-T004a Backend signed-token, policy, directory-failure/revocation and scoped persistence acceptance cases for F01-A01–07 (`nexusflow/tests/identity/`, 120 feature tests).
  - [ ] F01-T004b Cross-tenant HTTP/download/stream route acceptance once actual routes exist; retain the original endpoint coverage requirement.
- [ ] F01-T005 Integrate the three lanes and real-provider adapter tests in `nexusflow/modules/01-identity-environments/integration/`; request composition changes from integration owner. Prerequisites: F01-T002–004 and real-Entra test configuration for provider smoke. Acceptance: all isolated cases pass, no fake identity in deployment mode, consumer conformance matrix passes.

F01-T002 is complete at its backend provider boundary: token verification, fresh directory lookup through an injected port, persisted grants and logical environment registry are implemented in `nexusflow/modules/identity_environments/`. F01-T004a is independently verified; F01-T004 remains open for actual endpoint coverage. Live Microsoft JWKS/Graph adapters, React screens and deployed-provider conformance remain under F01-T003/F01-T005 and integration gates. See [implementation-identity-environments.md](../../implementation-identity-environments.md). Numbered paths above are ownership labels.
