# Owner-scoped debugger session discovery increment

Branch: `codex/041-debug-session-discovery`, based on main `078b6c6` (merged PR #38).

## Implemented boundary

The Dev debugger collection now lists only metadata owned by the freshly verified caller in an organization/environment/project. SQL selects only session ID and public session JSON, never request input, plan, placement or full frozen records. Listing queries no draft/configuration/runtime providers and performs no writes. Empty authorized project scopes return an empty page.

The response supplies items and next_cursor. Limits are 1–100, default 50. Ordering uses immutable recorded creation time descending and session ID descending, with an exclusive composite seek and SQL limit+one. Strict bounded URL-safe JSON cursors bind scope/project/owner, reject malformed/duplicate-key/foreign contexts and do not grant authority. Every page reapplies fresh permissions and owner filtering. Timestamp/row bindings and acknowledged run-ID bounds are checked before returning typed metadata. No table/schema migration, grants or persisted snapshot changes.

State remains dispatch_pending/started metadata, not live workflow status. Acknowledgment does not reorder entries; clients refresh the first page for new sessions. Discovery does not attest archived plan integrity; existing detail/control/inspection routes retain their full checks. Enabled local debugging gains the route automatically. See [ADR 0039](../../docs/decisions/0039-debug-session-discovery.md), [guide](../../docs/debug-session-discovery.md) and [API](../../docs/api.md).

## Parallel ownership and verification

Frozen interfaces preceded repository/cursor/unit, service/API/test and independent HTTP/actual CLI/Temporal lanes. The coordinator owns documentation, checklist reconciliation, cross-lane review and final regression.

Repository tests: 40 passed, including SQLite authorizer checks forbidding private-column reads, mixed owners/scopes/projects, tied timestamps, exclusive seek, new insertion, restart/acknowledgment stability, malformed cursors and corrupt/closed metadata failures. Review hardened corrupt-but-valid run metadata to the existing 1–256 character acknowledgment limit and pending None invariant. Signed API tests: 11 passed (7.18 seconds). Independent HTTP: 6 passed (4.94 seconds). New actual CLI/socket case: 1 passed (28.21 seconds), verifying two paused sessions, full restart, rediscovery without carrying a selected ID, reuse of a pre-restart cursor, selection/control/completion/replay and unchanged activation. Independent review found no blocking issues.

Full default regression: **1,606 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 48 integration tests deselected** (258.69 seconds). Full actual Temporal runtime suite: **48 passed, 198 deselected** (316.04 seconds), with ignored recoverable log/exit evidence. Ruff lint and formatting pass across 373 Python files; strict mypy passes across 93 source files. Source distribution and wheel build successfully; `git diff --check` passes. Final-source post-hardening CLI/socket restart discovery rerun: **1 passed** (24.62 seconds). Independent review found no blocking issues.

## Remaining scope

DB-T01e/T02e/T04e/T05d and NF-T011e are checked for metadata discovery only; full debugger parent tasks remain open. Browser session lists, search/filter UX, live status projections, general step-input/output inspection, cancellation/expiry, events, mocks, parallel stepping and production-provider acceptance remain outstanding. Local synthetic identities and actual Temporal establish only the stated backend/recovery behavior; no live Entra, browser or hosted deployment acceptance is claimed.
