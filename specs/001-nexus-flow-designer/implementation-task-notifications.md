# Human-task notification increment

Branch: `codex/020-task-notifications`, based on merged PR #17. Decision: [ADR 0018](../../docs/decisions/0018-task-notifications.md).

## Bounded scope

Persist one-shot reminder and escalation-kind schedules for existing human tasks. Scheduling requires trusted `task.create` authorization, a current expected revision and an aware due timestamp. Snapshot current unvoted recipients, enforce a maximum of 1,000 schedules per task and preserve immutable, idempotent schedule identity.

An internal worker explicitly processes a bounded due batch using an injected clock. Stable notification IDs support sink deduplication after uncertain delivery or restart. Failed delivery records retain bounded retry backoff and safe failure categories. Completion, reassignment and changes to remaining voters cancel stale pending notifications before sending. Notification payloads and audits contain no form values or workflow variables. Existing completion outbox behavior remains unchanged.

The sink callback runs under the SQLite writer transaction to serialize delivery eligibility with terminal transitions. Hosts must enforce bounded callback timeouts and prohibit task database reentry. This is a local at-least-once delivery contract, not production transport, high availability or a performance guarantee.

## Parallel ownership

The coordinator freezes notification models, table layout and task event interfaces before delegation. Separate sub-agents own notification contracts/service, independent acceptance tests, and this documentation plus a read-only review. The coordinator owns shared task schema/event integration, specification checklist reconciliation and aggregate verification. Agents do not synchronize shared dependencies or commit independently.

## Verification

Verified locally on Python 3.12 using the existing `.venv/Scripts/python.exe -m` interpreter:

- Full `pytest -q`: 753 passed, 1 Linux-only regression skipped on Windows, 11 integration tests deselected (55.00 seconds), including 18 notification acceptance cases.
- Ruff lint and formatting passed (214 files); strict mypy passed (68 source files).
- `uv build`: source distribution and wheel built successfully.
- `pytest -m integration nexusflow/tests/runtime -q`: 11 passed, 67 deselected (31.98 seconds), using the actual local Temporal CLI/server to preserve existing runtime behavior.
- `git diff --check`: passed.

Independent read-only review found no blocking issue in the additive task schema/events, scheduling identity, recipient validation or transaction boundaries. Acceptance covers safe payloads, authorization, scope isolation, scheduling limits, shared revisions, UTC due times, stable retry payloads, restart, terminal/stale snapshot cancellation and SQLite serialization with task decisions. Cancellation after an uncertain send prevents further attempts but does not establish that an earlier external delivery never happened.

Hosted CI has not run for this uncommitted increment. These runtime regressions and local fake-sink tests do not establish external connector delivery, automatic timers or actual Temporal human-task notification integration.

## Remaining scope

Automatic scheduler execution, repeating timers, escalation rerouting, notification configuration UI, connector transports and credentials, production delivery guarantees, Azure deployment, live Entra providers and Temporal human-task creation/completion remain open. There is no new HTTP endpoint or process startup behavior in this increment.
