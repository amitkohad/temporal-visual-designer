# Human tasks implementation tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Completed bounded slices are recorded separately from full feature acceptance. `[P]` lanes own disjoint paths and start after contract freeze. See [human-task core evidence](../../implementation-human-task-core.md).

- [ ] **HT-T01** Freeze state machine, approval policies, APIs and fake-provider fixtures in `nexusflow/modules/06-human-tasks/contracts/`. Prerequisite: [shared contracts](../../contracts.md), including identity/form/runtime ports frozen. Maps HT-FR-01–07, HT-A1–A7.
  - [x] **HT-T01a** Freeze bounded any-one/all-approver task/decision/completion contracts, fresh authorization and assignment/sink ports under `nexusflow/modules/human_tasks/types.py`; verify strict contract behavior independently ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T01c** Freeze inbox page/filter contracts and signed HTTP read/audit/decision boundaries with verified user object IDs ([evidence](../../implementation-human-task-api.md)).
  - [ ] **HT-T01b** Freeze remaining form/attachment/timer/notification, delegation/reassignment, inbox/API and sequential/conditional policy contracts.
- [ ] **HT-T02 [P]** Implement rules, assignment, task persistence, form validation, attachments, reminders, audit and completion outbox in `nexusflow/modules/06-human-tasks/service/`. Prerequisite HT-T01. Maps HT-FR-01–07 and backend portions HT-A1–A7; use fake upstream ports.
  - [x] **HT-T02a** Implement bounded approval service and SQLite repository with immutable assignments, fresh scope authorization, creation/command retries, revision CAS, atomic decision/audit/completion persistence and explicit retryable delivery ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T02c** Implement scope/assignment-filtered inbox pages, status/run filtering and request-bound HTTP task services using fresh authorization ([evidence](../../implementation-human-task-api.md)).
  - [ ] **HT-T02b** Complete remaining rules, forms/attachments, timers/notifications and task lifecycle/inbox behavior required by HT-FR-01–07.
- [ ] **HT-T03 [P]** Implement accessible inbox, task/form views, approval actions and delegation UI in `nexusflow/modules/06-human-tasks/ui/`. Prerequisite HT-T01. Maps HT-FR-02–05/07, HT-A1/A2/A5/A6; consume mock APIs without editing backend or form-authoring paths.
- [ ] **HT-T04 [P]** Implement independent fixtures, contract, UI and fault/concurrency tests in `nexusflow/modules/06-human-tasks/tests/`. Prerequisite HT-T01. Maps HT-A1–A7; use fake membership, scanning, storage, runtime, virtual time and explicit barriers.
  - [x] **HT-T04a** Verify bounded core independently under `nexusflow/tests/human_tasks/`, including authorization, retries, cross-connection races, transaction failures and completion restart/redelivery with an idempotent fake sink ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T04c** Verify inbox filtering before pagination, immutable group snapshots, reopened decision receipts, signed object-ID task operations and safe HTTP failures with actual local identity/task stores ([evidence](../../implementation-human-task-api.md)).
  - [ ] **HT-T04b** Complete remaining HT-A1–A7, UI, virtual timer, scanning/storage and actual-provider acceptance.
- [ ] **HT-T05** Integrate provider adapters and UI serially; record results in `nexusflow/modules/06-human-tasks/verification.md`. Prerequisites HT-T02–04 and compatible upstream provider builds. Maps HT-A1–A7. Run isolated tests first, then real-runtime completion recovery and applicable lint; avoid live Entra dependency in default suite.

  - [x] **HT-T05a** Compose bounded inbox/detail/audit/decision HTTP routes with actual local identity and task providers, explicit optional host configuration and per-request cleanup ([evidence](../../implementation-human-task-api.md)). Full runtime, forms and browser integration remain under HT-T05.
