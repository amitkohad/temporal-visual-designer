# Feature 08 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` means concurrently executable only after listed prerequisites, with ownership confined to the exact subtree. All tasks remain unchecked because this change is specifications only.

- [ ] F08-T01 Freeze `nexusflow/modules/08-connectors/contracts/` schemas against `specs/001-nexus-flow-designer/contracts.md`; prerequisites: shared contract review; acceptance: F08-A01–A05 mapped to fields/errors.
- [ ] F08-T02 [P] Implement provider adapters and dispatch boundaries in `nexusflow/modules/08-connectors/backend/`; prerequisites: F08-T01; acceptance: F08-A01–A05.
- [ ] F08-T03 [P] Implement schema forms, connection selection, and validation in `nexusflow/modules/08-connectors/ui/`; prerequisites: F08-T01; acceptance: F08-A01/A02/A04 using fake APIs.
- [ ] F08-T04 [P] Create fake providers, secret sentinels, and scenario suites in `nexusflow/modules/08-connectors/tests/`; prerequisites: F08-T01; acceptance: F08-A01–A05, including retry-after-acceptance and cancellation fixtures.
- [ ] F08-T05 Wire runtime and Designer ports in `nexusflow/modules/08-connectors/integration/`; prerequisites: F08-T02/F08-T03/F08-T04 and provider ports available; acceptance: F08-A01–A05 against connected modules.
- [ ] F08-T06 Record independent acceptance and integration results in `nexusflow/modules/08-connectors/integration/verification.md`; prerequisites: F08-T05; acceptance: all scenarios pass, zero secret leaks, applicable lint passes.
