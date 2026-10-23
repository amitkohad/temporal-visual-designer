# Human tasks implementation tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Tasks describe future work only. `[P]` lanes own disjoint paths and start after contract freeze.

- [ ] **HT-T01** Freeze state machine, approval policies, APIs and fake-provider fixtures in `nexusflow/modules/06-human-tasks/contracts/`. Prerequisite: [shared contracts](../../contracts.md), including identity/form/runtime ports frozen. Maps HT-FR-01–07, HT-A1–A7.
- [ ] **HT-T02 [P]** Implement rules, assignment, task persistence, form validation, attachments, reminders, audit and completion outbox in `nexusflow/modules/06-human-tasks/service/`. Prerequisite HT-T01. Maps HT-FR-01–07 and backend portions HT-A1–A7; use fake upstream ports.
- [ ] **HT-T03 [P]** Implement accessible inbox, task/form views, approval actions and delegation UI in `nexusflow/modules/06-human-tasks/ui/`. Prerequisite HT-T01. Maps HT-FR-02–05/07, HT-A1/A2/A5/A6; consume mock APIs without editing backend or form-authoring paths.
- [ ] **HT-T04 [P]** Implement independent fixtures, contract, UI and fault/concurrency tests in `nexusflow/modules/06-human-tasks/tests/`. Prerequisite HT-T01. Maps HT-A1–A7; use fake membership, scanning, storage, runtime, virtual time and explicit barriers.
- [ ] **HT-T05** Integrate provider adapters and UI serially; record results in `nexusflow/modules/06-human-tasks/verification.md`. Prerequisites HT-T02–04 and compatible upstream provider builds. Maps HT-A1–A7. Run isolated tests first, then real-runtime completion recovery and applicable lint; avoid live Entra dependency in default suite.
