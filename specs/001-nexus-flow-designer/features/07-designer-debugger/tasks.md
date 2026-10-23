# Designer debugger implementation tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Future tasks only. `[P]` marks disjoint ownership after contract freeze.

- [ ] **DB-T01** Freeze session/control/inspection/event and fake-runtime contracts in `nexusflow/modules/07-designer-debugger/contracts/`. Prerequisite: [shared contracts](../../contracts.md), frozen runtime gate and Designer snapshot ports. Maps DB-FR-01–07 and DB-A1–A7; resolve parallel step semantics before parallel work.
- [ ] **DB-T02 [P]** Implement sessions, authorization, durable command adapter, redaction, inspection and expiry in `nexusflow/modules/07-designer-debugger/service/`. Prerequisite DB-T01. Maps DB-FR-01–07, DB-A2–A7; use runtime fake until integration.
- [ ] **DB-T03 [P]** Implement panel, canvas overlay adapter, breakpoint controls and variable inspector in `nexusflow/modules/07-designer-debugger/ui/`. Prerequisite DB-T01. Maps DB-FR-02/03/05–07, DB-A1/A4/A5/A7. Use mock APIs and own no Designer-core paths.
- [ ] **DB-T04 [P]** Implement fake-provider, UI, security and recovery acceptance tests in `nexusflow/modules/07-designer-debugger/tests/`. Prerequisite DB-T01. Maps DB-A1–A7, including direct UAT/Prod API rejection and replayed commands.
- [ ] **DB-T05** Integrate panel/runtime adapters serially and record evidence in `nexusflow/modules/07-designer-debugger/verification.md`. Prerequisites DB-T02–04 plus compatible runtime/Designer providers. Maps DB-A1–A7; demonstrate durable pause across restart/continuation and run applicable lint. Cross-module fixes remain with their owners.
