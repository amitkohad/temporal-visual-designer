# Designer debugger implementation tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Bounded verified backend subtasks are checked below; full parents remain open. `[P]` marks disjoint ownership after contract freeze.

- [ ] **DB-T01** Freeze session/control/inspection/event and fake-runtime contracts in `nexusflow/modules/07-designer-debugger/contracts/`. Prerequisite: [shared contracts](../../contracts.md), frozen runtime gate and Designer snapshot ports. Maps DB-FR-01–07 and DB-A1–A7; resolve parallel step semantics before parallel work.
  - [x] **DB-T01d** Freeze explicit-public frozen workflow-input projection, protected/unsupported schema precedence, budgets and original-source digest contracts (ADR 0038).
  - [x] **DB-T01c** Freeze optional installation-bound local debugger profiles, read-only recovery validation and explicit CLI enablement contracts (ADR 0037).
  - [x] **DB-T01a** Freeze sequential proof-runtime Dev session/create/status/step/continue contracts, immutable snapshot/route ownership and durable command identity/receipts (ADR 0036).
- [ ] **DB-T02 [P]** Implement sessions, authorization, durable command adapter, redaction, inspection and expiry in `nexusflow/modules/07-designer-debugger/service/`. Prerequisite DB-T01. Maps DB-FR-01–07, DB-A2–A7; use runtime fake until integration.
  - [x] **DB-T02d** Implement pure bounded redaction and owner-authorized original-schema input inspection without runtime calls or writes ([evidence](../../implementation-debug-input-inspection.md)).
  - [x] **DB-T02a** Implement owner-scoped Dev sessions with frozen draft intents, integrity checks, exact-run acknowledgment, durable retry receipts and payload-free status/control ([evidence](../../implementation-designer-debug-sessions.md)).
- [ ] **DB-T03 [P]** Implement panel, canvas overlay adapter, breakpoint controls and variable inspector in `nexusflow/modules/07-designer-debugger/ui/`. Prerequisite DB-T01. Maps DB-FR-02/03/05–07, DB-A1/A4/A5/A7. Use mock APIs and own no Designer-core paths.
- [ ] **DB-T04 [P]** Implement fake-provider, UI, security and recovery acceptance tests in `nexusflow/modules/07-designer-debugger/tests/`. Prerequisite DB-T01. Maps DB-A1–A7, including direct UAT/Prod API rejection and replayed commands.
  - [x] **DB-T04d** Verify redaction/bounds, source binding, authorization and actual paused/worker-down/completed inspection with replay ([evidence](../../implementation-debug-input-inspection.md)).
  - [x] **DB-T04c** Verify local profile/schema/identity failures before startup, disabled defaults, grant preservation, provider cleanup and actual CLI/socket paused restart with step/continue/replay ([evidence](../../implementation-local-debugger.md)).
  - [x] **DB-T04a** Verify scoped owner/revocation/provider guards, frozen retries, command conflicts, quotas, corruption and exact-run adapter recovery; independently verify real Temporal pause/step/timer/continue/restart/replay ([evidence](../../implementation-designer-debug-sessions.md)).
- [ ] **DB-T05** Integrate panel/runtime adapters serially and record evidence in `nexusflow/modules/07-designer-debugger/verification.md`. Prerequisites DB-T02–04 plus compatible runtime/Designer providers. Maps DB-A1–A7; demonstrate durable pause across restart/continuation and run applicable lint. Cross-module fixes remain with their owners.
  - [x] **DB-T05c** Compose typed no-store input inspection on signed Dev sessions and verify the runnable example ([evidence](../../implementation-debug-input-inspection.md)).
  - [x] **DB-T05b** Wire the existing debugger into the owned local launcher through persistent sessions and the same trusted Temporal clients/journal; preserve normal execution and activation ([evidence](../../implementation-local-debugger.md)).
  - [x] **DB-T05a** Compose opt-in signed session APIs and actual Temporal adapter; verify immutable draft debugging without publication/activation using trusted local placement and explicit readiness fixtures ([evidence](../../implementation-designer-debug-sessions.md)).

Remaining debugger scope includes general variable/step-input/output inspection and redaction, cancellation/expiry, mock connections, event replay, parallel frontier semantics and React controls. The local launcher now supports opt-in debugger providers through the separately verified composition subset.
