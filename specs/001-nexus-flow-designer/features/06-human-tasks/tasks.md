# Human tasks implementation tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Completed bounded slices are recorded separately from full feature acceptance. `[P]` lanes own disjoint paths and start after contract freeze. See [human-task core evidence](../../implementation-human-task-core.md).

- [ ] **HT-T01** Freeze state machine, approval policies, APIs and fake-provider fixtures in `nexusflow/modules/06-human-tasks/contracts/`. Prerequisite: [shared contracts](../../contracts.md), including identity/form/runtime ports frozen. Maps HT-FR-01–07, HT-A1–A7.
  - [x] **HT-T01a** Freeze bounded any-one/all-approver task/decision/completion contracts, fresh authorization and assignment/sink ports under `nexusflow/modules/human_tasks/types.py`; verify strict contract behavior independently ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T01c** Freeze inbox page/filter contracts and signed HTTP read/audit/decision boundaries with verified user object IDs ([evidence](../../implementation-human-task-api.md)).
  - [x] **HT-T01d** Freeze explicit pre-decision reassignment/refresh commands, effective snapshot history, shared retry identity and optional resolver HTTP boundary ([evidence](../../implementation-task-reassignment.md)).
  - [x] **HT-T01e** Freeze trusted task-form binding, field-policy, preview/submission and shared revision contracts ([evidence](../../implementation-task-forms.md)).
  - [x] **HT-T01f** Freeze one-shot reminder/escalation schedules, immutable notification payloads, trusted sink and due-delivery contracts ([evidence](../../implementation-task-notifications.md)).
  - [x] **HT-T01g** Freeze ordered bounded stages, pinned stage assignments, stage-specific votes and current-stage access/receipt contracts ([evidence](../../implementation-sequential-approvals.md)).
  - [x] **HT-T01h** Freeze participant delegation commands, actor-bound historical receipts, current-stage transfer and before/after audit contracts ([evidence](../../implementation-task-delegation.md)).
  - [x] **HT-T01i** Freeze async completion delivery and trusted immutable exact-run/release destination contracts ([evidence](../../implementation-task-completion-delivery.md)).
  - [ ] **HT-T01b** Freeze remaining form integrations, attachment/timer/notification, remaining delegation policies/post-vote administrative reassignment, remaining inbox/API and remaining conditional policy contracts.
- [ ] **HT-T02 [P]** Implement rules, assignment, task persistence, form validation, attachments, reminders, audit and completion outbox in `nexusflow/modules/06-human-tasks/service/`. Prerequisite HT-T01. Maps HT-FR-01–07 and backend portions HT-A1–A7; use fake upstream ports.
  - [x] **HT-T02a** Implement bounded approval service and SQLite repository with immutable assignments, fresh scope authorization, creation/command retries, revision CAS, atomic decision/audit/completion persistence and explicit retryable delivery ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T02c** Implement scope/assignment-filtered inbox pages, status/run filtering and request-bound HTTP task services using fresh authorization ([evidence](../../implementation-human-task-api.md)).
  - [x] **HT-T02d** Implement scoped admin reassignment/refresh before voting with atomic snapshot/audit/receipt persistence, fresh permissions and bounded history ([evidence](../../implementation-task-reassignment.md)).
  - [x] **HT-T02e** Persist pinned task forms and values separately, validate submissions through the shared form engine, recheck field access atomically and guard approvals until valid submission ([evidence](../../implementation-task-forms.md)).
  - [x] **HT-T02f** Persist bounded notification schedules, UTC due times, stable delivery IDs, retry records and payload-free audits; cancel due terminal/stale-recipient records without altering workflow completion ([evidence](../../implementation-task-notifications.md)).
  - [x] **HT-T02g** Implement sequential any-one/all-approver stages with resolve-all snapshots, atomic handoffs, repeat actors, immediate rejection and final-only completion ([evidence](../../implementation-sequential-approvals.md)).
  - [x] **HT-T02h** Implement authorized transfer of an uncast current-stage vote, strict target resolution, atomic snapshot/audit/receipt persistence and shared assignment-change limits ([evidence](../../implementation-task-delegation.md)).
  - [x] **HT-T02i** Deliver pending completions asynchronously outside transactions, with atomic acknowledgment/audit and retry/cancellation recovery ([evidence](../../implementation-task-completion-delivery.md)).
  - [ ] **HT-T02b** Complete remaining rules, forms/attachments, timers/notifications and task lifecycle/inbox behavior required by HT-FR-01–07.
- [ ] **HT-T03 [P]** Implement accessible inbox, task/form views, approval actions and delegation UI in `nexusflow/modules/06-human-tasks/ui/`. Prerequisite HT-T01. Maps HT-FR-02–05/07, HT-A1/A2/A5/A6; consume mock APIs without editing backend or form-authoring paths.
- [ ] **HT-T04 [P]** Implement independent fixtures, contract, UI and fault/concurrency tests in `nexusflow/modules/06-human-tasks/tests/`. Prerequisite HT-T01. Maps HT-A1–A7; use fake membership, scanning, storage, runtime, virtual time and explicit barriers.
  - [x] **HT-T04a** Verify bounded core independently under `nexusflow/tests/human_tasks/`, including authorization, retries, cross-connection races, transaction failures and completion restart/redelivery with an idempotent fake sink ([evidence](../../implementation-human-task-core.md)).
  - [x] **HT-T04c** Verify inbox filtering before pagination, immutable group snapshots, reopened decision receipts, signed object-ID task operations and safe HTTP failures with actual local identity/task stores ([evidence](../../implementation-human-task-api.md)).
  - [x] **HT-T04d** Verify reassignment access removal, shared command conflicts, restart receipts, role denial, concurrent decision/reassignment and audit-failure rollback ([evidence](../../implementation-task-reassignment.md)).
  - [x] **HT-T04e** Verify task-form privacy, validation, policy/revision conflicts, binding retries, assignment removal, first-vote freeze, rollback and decision/submission races with real local stores ([evidence](../../implementation-task-forms.md)).
  - [x] **HT-T04f** Verify virtual-time delivery, retry/restart, lost acknowledgments, terminal/recipient cancellation, scope/auth, schedule limits, audit rollback and concurrent dispatcher ordering using real SQLite ([evidence](../../implementation-task-notifications.md)).
  - [x] **HT-T04g** Verify ordered rules, scope/actor handoff, receipt recovery, snapshot bounds, race/rollback, shared forms and stage-bound notification compatibility ([evidence](../../implementation-sequential-approvals.md)).
  - [x] **HT-T04h** Verify delegation authorization, retained votes, target constraints, scope/access removal, historical retries, stage isolation, mixed history caps, races and rollback ([evidence](../../implementation-task-delegation.md)).
  - [x] **HT-T04i** Verify async outbox concurrency/rollback and actual Temporal approval/rejection, lost acknowledgments, exact bindings and restart/replay ([evidence](../../implementation-task-completion-delivery.md)).
  - [ ] **HT-T04b** Complete remaining HT-A1–A7, UI, virtual timer, scanning/storage and actual-provider acceptance.
- [ ] **HT-T05** Integrate provider adapters and UI serially; record results in `nexusflow/modules/06-human-tasks/verification.md`. Prerequisites HT-T02–04 and compatible upstream provider builds. Maps HT-A1–A7. Run isolated tests first, then real-runtime completion recovery and applicable lint; avoid live Entra dependency in default suite.

  - [x] **HT-T05a** Compose bounded inbox/detail/audit/decision HTTP routes with actual local identity and task providers, explicit optional host configuration and per-request cleanup ([evidence](../../implementation-human-task-api.md)). Full runtime, forms and browser integration remain under HT-T05.

  - [x] **HT-T05b** Compose signed administrative reassignment HTTP requests with explicit trusted resolver configuration and safe missing-provider errors. This completes only the pre-decision reassignment slice of HT-A5; delegation and later lifecycle integration remain open ([evidence](../../implementation-task-reassignment.md)).

  - [x] **HT-T05c** Compose signed task-form preview/submission HTTP routes with explicit field policy and safe errors; preserve real Designer validator behavior. Runtime form resolution/mapping, attachments and browser integration remain open ([evidence](../../implementation-task-forms.md)).

  - [x] **HT-T05d** Verify sequential task handoff through signed existing decision/inbox APIs, shared form validation and stage-specific reminder cancellation. Workflow creation/completion bridge, UI and full HT-A1–A7 acceptance remain open ([evidence](../../implementation-sequential-approvals.md)).

  - [x] **HT-T05e** Verify signed participant delegation HTTP requests with existing providers, independent target authorization, shared-form freeze and stale-reminder cancellation; full UI/runtime/provider acceptance remains open ([evidence](../../implementation-task-delegation.md)).

  - [x] **HT-T05f** Integrate actual SQLite task decisions and async completion delivery with registered Temporal proof approval invocations; verify approval/rejection, receipt recovery and restart/replay. Automatic task creation, scheduling, browser and full bridge acceptance remain open ([evidence](../../implementation-task-completion-delivery.md)).
