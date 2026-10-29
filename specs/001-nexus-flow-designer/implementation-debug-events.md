# Metadata-only debug event replay increment

Branch: `codex/049-debug-event-replay`, based on main `59b6676` (merged PR #46).

## Implemented boundary

Add typed no-store signed Dev event reads at
`GET /projects/{project_id}/debug-sessions/{session_id}/events?after=0&limit=25`.
Every page checks current permission, original ownership, acknowledged dispatch,
placement and exact-run snapshot binding. No source/catalog/readiness reads or
workflow controls are performed.

The runtime records ordered metadata for step start/pause/wait/completion,
accepted commands and terminal outcomes. Fields are sequence, closed kind/state,
planned node ID and command revision. Inputs, outputs, error messages, timestamps
and actor IDs are absent. Duplicate successful and rejected commands add no
events. Completed-step event state is transient running without changing the
underlying workflow state or history timing.

Retain the latest 256 events, reconstructed through Temporal replay. Pages have
at most 25 items with exclusive sequence continuation and earliest/latest/gap
metadata. Strict validation checks complete retained-tail windows, contiguous
pages, revisions, planned nodes, token consistency and public response bounds.
Future/invalid cursors and malformed provider data fail safely. No database
migration, workflow history commands, plan or result-format changes.

On a retention gap, the tail is historical data rather than a replacement for
fresh status. Documentation describes a conservative stable-sequence baseline
procedure; an atomic public snapshot/cursor handoff and React reconciliation
remain open. This is pagination, not SSE/WebSocket streaming or a permanent
audit log, and depends on runtime/history availability.

See [ADR 0047](../../docs/decisions/0047-debug-event-replay.md) and
[reconnect guide](../../docs/debug-events.md).

## Parallel ownership and verification

Shared contracts were frozen before runtime/adapter, service/validation and
signed API/actual acceptance lanes. The coordinator owns contracts, docs,
checklists and final review/regression. Independent service/adapter privacy and
invariant reviews found no blocking issues.

Focused verification: 47 new service cases and 14 existing session cases passed
together (61, 5.55 seconds). New runtime/transport unit cases: 21 passed (2.12
seconds). Signed API: 19 passed (30.69 seconds). Five actual runtime scenarios
passed (27.83 seconds), including retention overflow through 98 real activity
boundaries, restart/replay, normal-run denial and payload-free terminal failures
and cancellation. The additional actual signed reconnect/retry/restart scenario
passed (9.47 seconds).

Ruff lint and formatting (430 Python files), mypy (97 source files), package
source/wheel build and `git diff --check` passed. Full default regression:
**2,035 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 71
integration tests deselected** (370.40 seconds). Full actual Temporal regression:
**71 passed, 236 deselected** (379.67 seconds). Ignored
`artifacts/test-results/event-temporal.log` and `event-temporal.json` record exit
code zero. No hosted CI or browser acceptance was run.

## Remaining scope

Atomic public reconciliation handoff, React event consumers, streaming, attempt
and detailed failure diagnostics, dynamic variables, parallel frontiers and
production telemetry remain open. Local signed identities and registered
activities do not establish production-provider or browser acceptance.
