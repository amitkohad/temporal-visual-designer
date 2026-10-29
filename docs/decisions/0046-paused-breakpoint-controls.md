# ADR 0046: Durable breakpoint replacement on paused Dev sessions

Status: accepted for the sequential proof debugger.

Add GET and POST `/projects/{project_id}/debug-sessions/{session_id}/breakpoints`.
GET returns the active breakpoint set and runtime revision. POST replaces the
whole set using command ID, current paused node ID, expected revision and at most
100 unique executable node IDs. Empty replacement clears the set. Requests are
canonicalized into sorted order; duplicate IDs are invalid.

Fresh owner/Dev authorization and saved placement/run binding apply to every
request. Replacement is allowed only at an acknowledged paused boundary before
a permit has been released. Runtime owns validation and ordering against other
debug controls through the same revision and command-ID ledger. Stale, foreign,
non-executable or conflicting changes fail safely.

Replacement modifies a runtime overlay initialized from the saved plan's
breakpoints. It does not edit the plan, original start request or workflow source.
It never releases the currently paused step, even when that node is removed from
the set. Step-over/continue remain separate commands. Changes affect future
boundary checks; they do not rewind completed nodes or interrupt running work.

Reuse the existing debugger command table with an unambiguous request union:
normal controls have `action`; breakpoint controls have `breakpoints`. Existing
stored requests and receipts remain unchanged. Both kinds share the same
session-wide ID namespace and 1,000-command budget. Exact successful retries
return their saved receipt after fresh authorization; changed bodies or command
kinds conflict, including while acknowledgement is pending.

The Temporal adapter pins query/update to the saved exact run, bounds RPCs and
uses a stable full-command update identity for lost-acknowledgement recovery.
New update handlers and the overlay preserve existing histories because the
initial overlay equals the original plan and old histories contain no breakpoint
replacement command. Full replay regression is required.

No database migration, plan/result format or metadata-only status changes.
Live breakpoint changes during running activities/timers/human waits, parallel
frontier policy, event streams and React controls remain open.
