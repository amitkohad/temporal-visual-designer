# Human-task approval core increment

Branch: `codex/016-human-task-core`, based on merged PR #13. Decision: [ADR 0014](../../docs/decisions/0014-human-task-core.md).

## Bounded implementation scope

Implement independently testable backend contracts, service and SQLite persistence for `any_one` and `all_approvers` approvals. Assignment resolution accepts Entra user/group IDs through an injected resolver and stores an immutable, deduplicated eligible-user snapshot. Trusted fresh authorization checks scope and actor eligibility for each operation. Task creation deduplicates the organization/environment/execution/node-invocation identity and rejects conflicting retries.

Decision commands use durable command IDs and expected revisions. Exact retries preserve their original result; conflicting command reuse and stale revisions fail. Distinct approvers contribute once, a rejection terminates the task, and all-approver approval requires the full pinned eligible set. Terminal decision persistence and a stable completion outbox record commit in one SQLite transaction with audit and command receipts.

Explicit completion delivery retries pending records through an injected sink. A failed sink leaves the record pending; a sink acknowledgment permits local acknowledgment. Crash recovery may deliver the same completion again, so the recipient's stable-ID deduplication is part of the port contract. This increment does not add a background dispatcher or establish real Temporal completion acceptance.

## Parallel ownership

Freeze feature-local types and port interfaces before dependent implementation. Run disjoint lanes for service/repository code, independent acceptance tests and decision/evidence documentation. The coordinator owns shared integration, dependency changes, aggregate validation and checklist reconciliation. Agents use the existing interpreter without concurrent environment synchronization.

## Acceptance plan

- Any-one approval, all-approver completion and immediate rejection select the expected terminal result.
- Assignment expansion deduplicates users and rejects empty results; task identity pins immutable assignment and invocation metadata.
- Creation retries return the original task; conflicting invocation reuse fails without mutation.
- Foreign scope, revoked placement authorization, ineligible actors and stale revisions fail without exposing protected task state or creating a completion.
- Exact command retries survive repository restart; conflicting command reuse and duplicate user decisions fail.
- Independent connections racing decisions produce valid revision transitions and one terminal completion.
- Persisted pending completions survive restart. Sink failure remains retryable; acknowledgment-after-acceptance failure can redeliver the same stable completion ID to a deduplicating fake sink.
- Audit, task state, command receipt and completion persistence remain atomic when a transaction fails.

## Evidence

Coordinator verification on Python 3.12:

- `.venv/Scripts/python.exe -m pytest nexusflow/tests/human_tasks -q`: 17 passed.
- `.venv/Scripts/python.exe -m pytest -q`: 639 passed, 1 Linux-only regression skipped, 11 integration tests deselected (34.66 seconds).
- Ruff lint and formatting passed (194 files); strict mypy passed (64 source files).
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

Independent implementation and acceptance agents reviewed the shared contracts and approval invariants with no blocking findings. Tests cover rejected cross-scope/ineligible/revoked access, immutable assignments, request/command collisions, recorded-response retries, rule outcomes, SQLite reopening, cross-connection races and rollback after an injected outbox write failure. Delivery tests cover sink failure and acknowledgment uncertainty with restart and stable-ID deduplication. These results are local/fake-port evidence, not hosted CI or actual Temporal/live Entra acceptance.

## Remaining feature acceptance

HT-T01a/T02a/T04a and NF-T010a are complete at the bounded backend/fake-port boundary. HT-T01/02/04 remain partial until their full contracts, backend and acceptance requirements are implemented. HT-T03 and HT-T05 remain open. This increment covers portions of HT-FR-01/02/03/06 and HT-A1/2/3/4 only.

Deferred: forms and field permissions, attachments/scanning, timers/reminders/escalation, notifications, sequential/conditional routing, request information, claims, delegation/reassignment, inbox/UI/pagination, HTTP exposure, live Entra assignment/authentication and real Temporal completion/recovery integration. Existing forms validators and proof runtime tests do not establish integration of these features with human tasks.
