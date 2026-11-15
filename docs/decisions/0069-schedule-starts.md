# ADR 0069: Timezone-aware schedule start contracts

Status: accepted for the RT-T01b schedule subset (create/list/disable + occurrence
fire into durable starts). Full interpreter, webhooks, checkpoints and projections
remain open under RT-T01b/RT-T03b parents.

## Decision

Scheduled starts reuse the existing durable start boundary ([ADR 0009](0009-durable-starts.md)).
Each due occurrence becomes a `StartRequest` with `source="schedule"` and a stable
occurrence idempotency key. Authorization, active-release snapshotting, input
fingerprinting and at-least-once dispatch semantics are unchanged.

### Definition

A schedule record is scoped to organization/environment/project/workflow and carries:

- `schedule_id` — Identifier; unique within the scope
- `cron` — five-field crontab (`minute hour day-of-month month day-of-week`);
  supports lists, ranges, steps and `*`; rejects seconds, names and six/seven-field forms
- `timezone` — IANA zone (`zoneinfo`); invalid zones are typed `invalid_timezone`
- `overlap_policy` — `allow` or `skip` only (`queue` deferred)
- `input_json` — fixed payload for every occurrence (same conflict rules as manual starts)
- `enabled` — disabled schedules never fire; create/list/disable are the mutating surface

### Occurrence identity

Idempotency key: `{schedule_id}.{YYYYMMDDTHHMMSSZ}` where the timestamp is the
occurrence's civil time converted to UTC and truncated to whole seconds. The
combined key must remain an Identifier (≤128). The start ledger primary key
`(org, env, project, workflow, source, key)` therefore deduplicates the occurrence
across retries and launcher restarts.

### Overlap

- `allow` — every due occurrence may start; keys differ so they never conflict
- `skip` — while a prior occurrence for the same schedule still has an undelivered
  start intent (`dispatched=0`), later due occurrences are recorded as skipped and
  do not call the start service. After the prior intent is acknowledged, the next
  due occurrence may fire. Workflow-lifetime overlap (Temporal still running) is
  out of scope for this increment.

### Errors

Typed schedule errors: `invalid_input`, `invalid_timezone`, `not_found`, `conflict`,
`forbidden`, `unavailable`. Start-path conflicts for reused occurrence keys with
changed payloads continue to surface as start `conflict`.

### Local enablement

`nexusflow-local enable-schedules` writes a Dev-only profile marker and schedules
database. `serve` loads the profile, exposes signed HTTP create/list/disable and
runs an owned poll loop that fires due occurrences through the existing start
pipeline. Without the profile, schedule APIs are unavailable.

## Non-claims

No Entra, cloud Temporal Schedule API, webhook/form start adapters, queue overlap,
checkpoints, projections or Designer palette. This slice does not complete RT-T01b
or RT-T03b parents.
